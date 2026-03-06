import sys
import os
import re
import unicodedata
import frontmatter
from pathlib import Path
import spacy
import yaml

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

logger = setup_logger()
git = GitHandler()

# Charger le modèle NER Français — essayer le modèle large d'abord, puis le moyen
try:
    nlp = spacy.load("fr_core_news_lg")
except OSError:
    try:
        nlp = spacy.load("fr_core_news_md")
        logger.warning("Modèle fr_core_news_lg indisponible, utilisation de fr_core_news_md")
    except OSError:
        logger.error("Aucun modèle Spacy français trouvé. Lancez: python -m spacy download fr_core_news_lg")
        sys.exit(1)

# Charger la config pour les patterns à ignorer
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

IGNORE_PATTERNS = set(CONFIG.get('linking', {}).get('ignore_patterns', []))

# Longueur minimale pour qu'un nom d'entité soit considéré pour le linking
MIN_ENTITY_NAME_LENGTH = CONFIG.get('linking', {}).get('min_entity_name_length', 4)

# Index de toutes les entités connues pour le linking
# Format: { "nom_normalisé": {"path": "chemin/fichier.md", "display": "Nom Complet"} }
ENTITY_INDEX = {}

# Index inversé : chemin fichier -> liste de noms/alias
ALIAS_INDEX = {}

# Backlinks : entité -> set des fichiers qui la référencent
BACKLINKS = {}

# Nombre max de passes récursives
MAX_LINK_PASSES = 3

# Regex pour identifier les zones protégées qui ne doivent pas recevoir de liens
# (blocs de code, URLs, sections source, titres markdown)
_PROTECTED_ZONES_RE = re.compile(
    r'```.*?```'               # blocs de code
    r'|`[^`]+`'                # code inline
    r'|https?://\S+'           # URLs
    r'|\*\*Source\*\*\s*:.*$'  # lignes de source/attribution
    r'|^#{1,6}\s+.*$',        # titres markdown
    re.MULTILINE | re.DOTALL
)


def normalize_name(name: str) -> str:
    """Normalise un nom pour la comparaison : minuscules, sans accents, sans tirets."""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().replace(" ", "_").replace("-", "_").replace("'", "").strip("_")


def build_entity_index():
    """Construit un index enrichi de toutes les entités existantes pour le linking.
    
    Indexe le nom principal, le nom de fichier, et les alias éventuels du frontmatter.
    """
    logger.info("🔍 Construction de l'index des entités...")
    md_files = list(Path(".").rglob("*.md"))
    exclude_dirs = {".git", "scripts", "config", "admin", "rapports"}

    for f in md_files:
        if any(part in exclude_dirs for part in f.parts):
            continue
        if f.name == "README.md":
            continue

        try:
            post = frontmatter.load(f)
        except Exception:
            continue

        rel_path = str(f.relative_to(Path(".")))

        # Nom principal
        name = post.get('nom_complet', post.get('nom', f.stem))
        display_name = name

        # Enregistrer le nom principal
        norm = normalize_name(name)
        if norm and norm not in IGNORE_PATTERNS:
            ENTITY_INDEX[norm] = {"path": rel_path, "display": display_name}

        # Enregistrer le stem du fichier comme alias
        stem_norm = normalize_name(f.stem)
        if stem_norm and stem_norm != norm and stem_norm not in IGNORE_PATTERNS:
            ENTITY_INDEX[stem_norm] = {"path": rel_path, "display": display_name}

        # Alias depuis le frontmatter (champ 'alias' ou 'aliases')
        aliases = post.get('alias', post.get('aliases', []))
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases:
            alias_norm = normalize_name(alias)
            if alias_norm and alias_norm not in IGNORE_PATTERNS:
                ENTITY_INDEX[alias_norm] = {"path": rel_path, "display": display_name}

        # Nom court pour les organisations
        nom_court = post.get('nom_court', '')
        if nom_court:
            nc_norm = normalize_name(nom_court)
            if nc_norm and nc_norm not in IGNORE_PATTERNS:
                ENTITY_INDEX[nc_norm] = {"path": rel_path, "display": display_name}

    logger.info(f"✅ Index construit : {len(ENTITY_INDEX)} entrées")


def _get_protected_ranges(content: str) -> list:
    """Calcule les plages de texte protégées (code, URLs, sources, titres).
    
    Ces zones ne doivent pas recevoir de liens pour éviter de créer
    des associations trompeuses dans des contextes non-éditoriaux.
    """
    ranges = []
    for m in _PROTECTED_ZONES_RE.finditer(content):
        ranges.append((m.start(), m.end()))
    return ranges


def _is_in_protected_zone(start: int, end: int, protected_ranges: list) -> bool:
    """Vérifie si une position chevauche une zone protégée."""
    for pstart, pend in protected_ranges:
        if start < pend and end > pstart:
            return True
    return False


def _is_inside_link(content: str, start: int, end: int) -> bool:
    """Vérifie si la position start..end est déjà à l'intérieur d'un lien [[...]]."""
    # Cherche le [[ le plus proche avant start
    before = content[:start]
    last_open = before.rfind('[[')
    last_close = before.rfind(']]')
    if last_open > last_close:
        # On est à l'intérieur d'un [[ ... (pas encore fermé)
        return True
    return False


def link_document(file_path):
    """Parcourt un document et crée des liens wiki [[...]] vers d'autres entités.
    
    Utilise Spacy NER + correspondance directe dans l'index. Ne relie que la première
    occurrence de chaque entité. Ignore les mentions déjà liées.
    
    Principes éthiques appliqués :
    - Ne crée des liens que vers des entités documentées dans le dépôt
    - Ne lie pas à l'intérieur de blocs de code, URLs, sections source ou titres
    - Requiert une longueur minimale de nom pour éviter les faux positifs
    - Ne crée pas d'auto-liens (une fiche ne pointe pas vers elle-même)
    """
    post = frontmatter.load(file_path)
    content = post.content
    doc = nlp(content)

    # Calculer les zones protégées une seule fois
    protected_ranges = _get_protected_ranges(content)

    modified = False
    already_linked = set()  # Entités déjà liées dans ce document

    # Collecter les entités NER triées par longueur décroissante (greedy match)
    ner_entities = []
    for ent in doc.ents:
        if ent.label_ in ["PERSON", "ORG", "LOC", "MISC"]:
            text = ent.text.strip()
            if len(text) < MIN_ENTITY_NAME_LENGTH:
                continue
            norm_text = normalize_name(text)
            if norm_text in IGNORE_PATTERNS:
                continue
            ner_entities.append((text, norm_text))

    # Trier par longueur du texte décroissante pour éviter les sous-chaînes
    ner_entities.sort(key=lambda x: len(x[0]), reverse=True)

    # Aussi chercher les noms d'entités de l'index directement dans le contenu
    # (pour les cas où Spacy ne détecte pas une entité connue)
    for norm_key, entry in ENTITY_INDEX.items():
        display = entry["display"]
        if len(display) >= MIN_ENTITY_NAME_LENGTH and display in content and normalize_name(display) not in already_linked:
            if display not in [e[0] for e in ner_entities]:
                ner_entities.append((display, norm_key))

    for text, norm_text in ner_entities:
        if norm_text in already_linked:
            continue

        # Vérification si l'entité existe dans notre index
        if norm_text not in ENTITY_INDEX:
            continue

        entry = ENTITY_INDEX[norm_text]
        target_file = entry["path"]
        display_name = entry["display"]

        # Ne pas auto-lier vers soi-même
        self_path = str(file_path.relative_to(Path("."))) if file_path.is_relative_to(Path(".")) else str(file_path)
        if target_file == self_path:
            continue

        wiki_link = f"[[{display_name}]]"

        # Vérifier si déjà lié
        if wiki_link in content:
            already_linked.add(norm_text)
            continue

        # Trouver la première occurrence qui n'est PAS déjà dans un lien
        # et qui n'est PAS dans une zone protégée (source, URL, code, titre)
        search_start = 0
        replaced = False
        while search_start < len(content):
            idx = content.find(text, search_start)
            if idx == -1:
                break
            end_idx = idx + len(text)
            if _is_inside_link(content, idx, end_idx):
                search_start = end_idx
                continue
            if _is_in_protected_zone(idx, end_idx, protected_ranges):
                search_start = end_idx
                continue
            # Remplacer cette occurrence
            content = content[:idx] + wiki_link + content[end_idx:]
            # Recalculer les zones protégées après modification
            protected_ranges = _get_protected_ranges(content)
            replaced = True
            break

        if replaced:
            modified = True
            already_linked.add(norm_text)
            logger.debug(f"🔗 Lien créé : {text} -> [[{display_name}]]")

            # Backlinks
            if display_name not in BACKLINKS:
                BACKLINKS[display_name] = set()
            BACKLINKS[display_name].add(str(file_path))

    if modified:
        post.content = content
        with open(file_path, 'w', encoding='utf-8') as f:
            frontmatter.dump(post, f)

    return modified


def update_backlinks_in_frontmatter():
    """Met à jour le champ 'liens' du frontmatter avec les backlinks découverts."""
    logger.info("🔄 Mise à jour des backlinks dans le frontmatter...")
    updated = 0
    for entity_name, referencing_files in BACKLINKS.items():
        # Trouver le fichier de cette entité
        norm = normalize_name(entity_name)
        if norm not in ENTITY_INDEX:
            continue
        entity_path = Path(ENTITY_INDEX[norm]["path"])
        if not entity_path.exists():
            continue

        try:
            post = frontmatter.load(entity_path)
            existing_links = set(post.get('liens', []) or [])
            new_links = set()

            for ref_file in referencing_files:
                ref_path = Path(ref_file)
                if ref_path == entity_path:
                    continue
                try:
                    ref_post = frontmatter.load(ref_path)
                    ref_name = ref_post.get('nom_complet', ref_post.get('nom', ref_path.stem))
                    new_links.add(ref_name)
                except Exception:
                    new_links.add(ref_path.stem)

            combined = existing_links | new_links
            if combined != existing_links:
                post['liens'] = sorted(combined)
                with open(entity_path, 'w', encoding='utf-8') as f:
                    frontmatter.dump(post, f)
                updated += 1
        except Exception as e:
            logger.warning(f"⚠️ Erreur backlink pour {entity_name}: {e}")

    logger.info(f"✅ {updated} fiches mises à jour avec des backlinks")


def main():
    build_entity_index()

    exclude_dirs = {".git", "scripts", "config", "admin", "rapports"}
    md_files = list(Path(".").rglob("*.md"))
    md_files = [f for f in md_files
                if not any(part in exclude_dirs for part in f.parts)
                and f.name != "README.md"]

    total_links_created = 0

    # Passes récursives : chaque passe peut révéler de nouveaux liens
    for pass_num in range(1, MAX_LINK_PASSES + 1):
        logger.info(f"🔄 Passe de linking {pass_num}/{MAX_LINK_PASSES}...")
        total_modified = 0
        for f in md_files:
            if link_document(f):
                total_modified += 1
        total_links_created += total_modified
        logger.info(f"   ➡️ {total_modified} fichiers modifiés lors de la passe {pass_num}")
        if total_modified == 0:
            logger.info(f"✅ Convergence atteinte à la passe {pass_num}, aucun nouveau lien")
            break

    # Mise à jour des backlinks dans le frontmatter
    update_backlinks_in_frontmatter()

    logger.info(f"📊 Résumé : {total_links_created} fichiers modifiés au total, "
                f"{len(BACKLINKS)} entités avec des backlinks")

    # Ne commiter que si le script n'est pas exécuté par GitHub Actions
    # (le workflow gère le commit/push lui-même)
    if not os.environ.get("GITHUB_ACTIONS"):
        git.commit_changes("feat: génération automatique des liens et backlinks (multi-passes)")
    else:
        logger.info("🔄 Exécution CI détectée — le commit sera géré par le workflow")

if __name__ == "__main__":
    main()

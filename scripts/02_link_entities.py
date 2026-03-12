import sys
import os
import re
import json
import unicodedata
import frontmatter
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote
import spacy
import yaml

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

logger = setup_logger()
git = GitHandler()

# Charger le modele NER Francais
try:
    nlp = spacy.load("fr_core_news_lg")
except OSError:
    try:
        nlp = spacy.load("fr_core_news_md")
        logger.warning("Modele fr_core_news_lg indisponible, utilisation de fr_core_news_md")
    except OSError:
        logger.error("Aucun modele Spacy francais trouve. Lancez: python -m spacy download fr_core_news_lg")
        sys.exit(1)

# Charger la config pour les patterns a ignorer
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

IGNORE_PATTERNS = set(CONFIG.get('linking', {}).get('ignore_patterns', []))

# Longueur minimale pour qu'un nom d'entite soit considere pour le linking
MIN_ENTITY_NAME_LENGTH = CONFIG.get('linking', {}).get('min_entity_name_length', 4)

# Index de toutes les entites connues pour le linking
# Format: { "nom_normalise": {"path": "chemin/fichier.md", "display": "Nom Complet"} }
ENTITY_INDEX = {}

# Index inverse : chemin fichier -> liste de noms/alias
ALIAS_INDEX = {}

# Backlinks : entite -> set des fichiers qui la referencent
BACKLINKS = {}

# Nombre max de passes recursives
MAX_LINK_PASSES = 3

# Index des ecoles connues (education -> fichier ecole)
ECOLE_INDEX = {}

# Timeout HTTP pour les requetes API
HTTP_TIMEOUT = 20

# Regex pour identifier les zones protégées qui ne doivent pas recevoir de liens
# (blocs de code, URLs, sections source, titres markdown)
# Note: on utilise [\s\S] au lieu de . pour les blocs de code multi-lignes
# afin de pouvoir utiliser re.MULTILINE sans re.DOTALL (qui casserait .*$ dans
# les patterns de titres et de sources)
_PROTECTED_ZONES_RE = re.compile(
    r'```[\s\S]*?```'          # blocs de code (multi-lignes)
    r'|`[^`]+`'                # code inline
    r'|https?://\S+'           # URLs
    r'|\*\*Source\*\*\s*:.*$'  # lignes de source/attribution
    r'|^#{1,6}\s+.*$',        # titres markdown
    re.MULTILINE
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
    logger.info("Construction de l'index des entités...")
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

    logger.info(f"Index construit : {len(ENTITY_INDEX)} entrées")


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
            logger.debug(f"Lien créé : {text} -> [[{display_name}]]")

            # Backlinks
            if display_name not in BACKLINKS:
                BACKLINKS[display_name] = set()
            BACKLINKS[display_name].add(str(file_path))

    if modified:
        post.content = content
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)

    return modified


def update_backlinks_in_frontmatter():
    """Met à jour le champ 'liens' du frontmatter avec les backlinks découverts."""
    logger.info("Mise à jour des backlinks dans le frontmatter...")
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
                with open(entity_path, 'wb') as f:
                    frontmatter.dump(post, f)
                updated += 1
        except Exception as e:
            logger.warning(f"Erreur backlink pour {entity_name}: {e}")

    logger.info(f"{updated} fiches mises a jour avec des backlinks")


# ---------------------------------------------------------------------------
# Enrichissement depuis les donnees publiques
# ---------------------------------------------------------------------------

def build_ecole_index():
    """Construit un index des ecoles pour enrichir les liens via le champ education."""
    ecoles_dir = Path("écoles")
    if not ecoles_dir.exists():
        return

    for f in ecoles_dir.glob("*.md"):
        try:
            post = frontmatter.load(f)
            name = post.get("nom_complet", f.stem.replace("-", " "))
            rel_path = str(f.relative_to(Path(".")))
            # Indexer le nom complet et ses variantes
            ECOLE_INDEX[name.lower()] = {"path": rel_path, "display": name}
            # Indexer aussi sans accents
            norm = normalize_name(name)
            ECOLE_INDEX[norm] = {"path": rel_path, "display": name}
            # Indexer les acronymes courants
            words = name.split()
            if len(words) > 2:
                acronym = "".join(w[0] for w in words if w[0].isupper())
                if len(acronym) >= 2:
                    ECOLE_INDEX[acronym.lower()] = {"path": rel_path, "display": name}
        except Exception:
            continue

    logger.info(f"Index ecoles construit : {len(ECOLE_INDEX)} entrees")


def enrich_education_links():
    """
    Parcourt les fiches personnes et cree des liens [[...]] vers les ecoles
    en se basant sur le champ education du frontmatter.
    Source : donnees internes du depot (champ education des fiches).
    """
    if not ECOLE_INDEX:
        return 0

    logger.info("Enrichissement des liens education -> ecoles...")
    personnes_dir = Path("personnes")
    if not personnes_dir.exists():
        return 0

    enriched = 0
    for f in personnes_dir.glob("*.md"):
        try:
            post = frontmatter.load(f)
            education = post.get("education", "")
            if not education:
                continue

            content = post.content or ""
            modified = False

            # Chercher les ecoles mentionnees dans le champ education
            edu_str = str(education)
            for ecole_key, ecole_info in ECOLE_INDEX.items():
                display = ecole_info["display"]
                wiki_link = f"[[{display}]]"

                # Verifier si l'ecole est mentionnee dans education
                if ecole_key in edu_str.lower() or display.lower() in edu_str.lower():
                    # Ajouter le lien dans le contenu si pas deja present
                    if wiki_link not in content and display in content:
                        # Remplacer la premiere mention dans le body
                        idx = content.find(display)
                        if idx >= 0 and not _is_inside_link(content, idx, idx + len(display)):
                            content = content[:idx] + wiki_link + content[idx + len(display):]
                            modified = True

                    # Ajouter aux backlinks
                    if display not in BACKLINKS:
                        BACKLINKS[display] = set()
                    BACKLINKS[display].add(str(f))

            if modified:
                post.content = content
                with open(f, 'wb') as fh:
                    frontmatter.dump(post, fh)
                enriched += 1

        except Exception as e:
            logger.debug(f"Erreur education link {f.name}: {e}")

    logger.info(f"  {enriched} fiches enrichies avec liens ecoles")
    return enriched


def enrich_metadata_links():
    """
    Cree des liens entre personnes basees sur les metadonnees partagees :
    - Meme ecole (education)
    - Meme tags/keywords
    - Mentionnes dans les memes institutions

    Source : donnees internes du depot.
    """
    logger.info("Enrichissement des liens via metadonnees partagees...")
    personnes_dir = Path("personnes")
    if not personnes_dir.exists():
        return 0

    # Construire un index inverse : institution -> liste de personnes
    institution_members = {}

    for f in personnes_dir.glob("*.md"):
        try:
            post = frontmatter.load(f)
            name = post.get("nom_complet", f.stem.replace("-", " "))

            # Indexer par education
            education = str(post.get("education", "") or "")
            if education:
                for edu_item in re.split(r'[,;]', education):
                    edu_item = edu_item.strip()
                    if len(edu_item) > 3:
                        key = edu_item.lower()
                        if key not in institution_members:
                            institution_members[key] = []
                        institution_members[key].append(name)

            # Indexer par tags lies a des institutions
            tags = post.get("tags", []) or []
            for tag in tags:
                if tag not in ("elite", "a_valider") and len(str(tag)) > 3:
                    key = str(tag).lower()
                    if key not in institution_members:
                        institution_members[key] = []
                    institution_members[key].append(name)

        except Exception:
            continue

    # Creer des liens pour les personnes qui partagent une institution
    enriched = 0
    for institution, members in institution_members.items():
        if len(members) < 2 or len(members) > 50:
            continue  # Ignorer les groupes trop grands ou trop petits

        for member_name in members:
            norm = normalize_name(member_name)
            if norm in ENTITY_INDEX:
                if member_name not in BACKLINKS:
                    BACKLINKS[member_name] = set()
                for other_name in members:
                    if other_name != member_name:
                        other_norm = normalize_name(other_name)
                        if other_norm in ENTITY_INDEX:
                            BACKLINKS[member_name].add(
                                ENTITY_INDEX[other_norm]["path"]
                            )
                            enriched += 1

    logger.info(f"  {enriched} liens de proximite trouves via metadonnees partagees")
    return enriched


def fetch_rne_party_affiliations():
    """
    Recupere les affiliations partisanes depuis le Repertoire National des Elus (RNE)
    via l'API data.gouv.fr. Source officielle et publique.

    Ne recupere que les donnees factuelles : nom, prenom, nuance politique.
    Aucune donnee sensible ou opinion.
    """
    logger.info("Recuperation des affiliations partisanes depuis le RNE (data.gouv.fr)...")

    # API tabulaire du RNE sur data.gouv.fr
    # Dataset des elus locaux avec leurs nuances politiques
    base_url = "https://tabular-api.data.gouv.fr/api/resources/d5f400de-ae3f-4966-8cb6-a85c70c6c24a/data/"
    params = "?page_size=100&page=1"

    affiliations = {}
    page = 1
    total_fetched = 0
    max_pages = 50  # Limiter pour ne pas surcharger l'API

    while page <= max_pages:
        url = f"{base_url}?page_size=100&page={page}"
        try:
            headers = {
                "User-Agent": "FrenchConnexion/1.0 (research; open-data)",
                "Accept": "application/json",
            }
            req = Request(url, headers=headers)
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError) as e:
            logger.warning(f"[WARN] Erreur RNE page {page}: {e}")
            break

        results = data.get("data", [])
        if not results:
            break

        for row in results:
            nom = row.get("Nom de l'élu", row.get("nom", "")).strip()
            prenom = row.get("Prénom de l'élu", row.get("prenom", "")).strip()
            nuance = row.get("Libellé de la nuance politique",
                           row.get("nuance_politique", "")).strip()

            if nom and prenom and nuance:
                full_name = f"{prenom} {nom}"
                affiliations[full_name.lower()] = nuance
                total_fetched += 1

        # Pagination
        next_page = data.get("next")
        if not next_page:
            break
        page += 1

    logger.info(f"  {total_fetched} affiliations partisanes recuperees")
    return affiliations


def apply_party_affiliations(affiliations):
    """
    Enrichit les fiches personnes avec l'affiliation partisane depuis le RNE.
    Ajoute le tag du parti et cree un lien [[parti]] dans le contenu.
    """
    if not affiliations:
        return 0

    logger.info("Application des affiliations partisanes...")
    personnes_dir = Path("personnes")
    if not personnes_dir.exists():
        return 0

    enriched = 0
    for f in personnes_dir.glob("*.md"):
        try:
            post = frontmatter.load(f)
            name = post.get("nom_complet", f.stem.replace("-", " "))
            name_lower = name.lower()

            if name_lower not in affiliations:
                continue

            nuance = affiliations[name_lower]
            keywords = post.get("keywords", []) or []
            tags = post.get("tags", []) or []
            content = post.content or ""

            modified = False

            # Ajouter la nuance politique aux keywords si pas deja present
            if nuance not in keywords:
                keywords.append(nuance)
                post["keywords"] = keywords
                modified = True

            # Ajouter le tag source-rne
            if "source-rne" not in tags:
                tags.append("source-rne")
                post["tags"] = tags
                modified = True

            if modified:
                with open(f, 'wb') as fh:
                    frontmatter.dump(post, fh)
                enriched += 1

        except Exception as e:
            logger.debug(f"Erreur affiliation {f.name}: {e}")

    logger.info(f"  {enriched} fiches enrichies avec affiliations partisanes")
    return enriched


def main():
    build_entity_index()
    build_ecole_index()

    exclude_dirs = {".git", "scripts", "config", "admin", "rapports"}
    md_files = list(Path(".").rglob("*.md"))
    md_files = [f for f in md_files
                if not any(part in exclude_dirs for part in f.parts)
                and f.name != "README.md"]

    total_files_modified = 0

    # Passes recursives : chaque passe peut reveler de nouveaux liens
    for pass_num in range(1, MAX_LINK_PASSES + 1):
        logger.info(f"Passe de linking {pass_num}/{MAX_LINK_PASSES}...")
        total_modified = 0
        for f in md_files:
            if link_document(f):
                total_modified += 1
        total_files_modified += total_modified
        logger.info(f"   -> {total_modified} fichiers modifies lors de la passe {pass_num}")
        if total_modified == 0:
            logger.info(f"Convergence atteinte a la passe {pass_num}, aucun nouveau lien")
            break

    # Enrichissement education -> ecoles
    enrich_education_links()

    # Enrichissement via metadonnees partagees (meme ecole, memes tags)
    enrich_metadata_links()

    # Enrichissement affiliations partisanes depuis le RNE (source officielle)
    try:
        affiliations = fetch_rne_party_affiliations()
        apply_party_affiliations(affiliations)
    except Exception as e:
        logger.warning(f"[WARN] Enrichissement RNE echoue (non bloquant): {e}")

    # Mise a jour des backlinks dans le frontmatter
    update_backlinks_in_frontmatter()

    logger.info(f"Resume : {total_files_modified} fichiers modifies au total, "
                f"{len(BACKLINKS)} entites avec des backlinks")

    # Ne commiter que si le script n'est pas execute par GitHub Actions
    # (le workflow gere le commit/push lui-meme)
    if not os.environ.get("GITHUB_ACTIONS"):
        git.commit_changes("feat: generation automatique des liens et backlinks (multi-passes)")
    else:
        logger.info("Execution CI detectee -- le commit sera gere par le workflow")

if __name__ == "__main__":
    main()

"""
Normalisation des fiches personnes vers un schema uniforme.

Ce script parcourt toutes les fiches du dossier personnes/ et uniformise
le frontmatter YAML pour que chaque fiche suive exactement le meme schema.
Concu pour fonctionner sur 10 000+ fichiers avec sauvegardes intermediaires.

Usage:
    python scripts/08_normalize_fiches.py [--dry-run] [--batch-size N]
    python scripts/08_normalize_fiches.py --test
"""

import sys
import os
import re
import argparse
import unicodedata
import subprocess
import frontmatter
from pathlib import Path
from datetime import date
from collections import OrderedDict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger

logger = setup_logger()

# ---------------------------------------------------------------------------
# Schema canonique pour les fiches Personne
# ---------------------------------------------------------------------------
# Champs obligatoires (toujours presents, meme vides)
CANONICAL_FIELDS_PERSON = [
    "type",
    "nom_complet",
    "birth_date",
    "birth_place",
    "nationality",
    "genre",
    "occupation",
    "education",
    "summary",
    "keywords",
    "sources",
    "tags",
    "statut_note",
    "date_creation_note",
]

# Champs optionnels (presents uniquement si non vides)
OPTIONAL_FIELDS_PERSON = [
    "death_date",
    "death_place",
    "website",
    "wikidata_id",
    "wikipedia_enriched",
    "aggregated_from",
    "liens",
]

# Correspondance champs anciens -> champs canoniques
FIELD_MAPPING = {
    "date_naissance": "birth_date",
    "lieu_naissance": "birth_place",
    "nationalite": "nationality",
    "birth_date_wikidata": "birth_date",
    "death_date_wikidata": "death_date",
    "formation": "education",
    "alma_mater": "education",
    "website_wikidata": "website",
    "nom_naissance": None,
}

# Champs a deplacer dans le corps du texte (pas dans le frontmatter)
FIELDS_TO_BODY = [
    "bio",
    "carriere",
    "affiliations",
    "distinctions",
    "famille",
    "presse",
    "controverses",
    "relations_detaillees",
]

# Champs de metadonnees internes a supprimer
FIELDS_TO_DROP = [
    "prenoms",
    "statut",
    "found_via",
    "search_depth",
    "original_query",
    "validation_score",
    "validation_reason",
    "verification_date",
    "verification_status",
    "wikipedia_content_length",
    "institutions_count",
    "relationships_count",
    "niveau_notoriete",
    "hatvp_declared",
    "positions_wikidata",
    "company",
    "industry",
    "founded",
    "headquarters",
    "leader",
]

# Mois en francais pour la conversion de dates
MOIS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}

BATCH_SIZE_DEFAULT = 500
PERSONNES_DIR = Path("personnes")


def strip_accents(s):
    """Supprime les accents pour normaliser les comparaisons."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def parse_french_date(value):
    """Convertit une date en format ISO YYYY-MM-DD si possible."""
    if not value or not isinstance(value, str):
        return value

    value = value.strip()

    # Deja au format ISO
    if re.match(r'^\d{4}-\d{2}-\d{2}$', value):
        return value
    if re.match(r'^\d{4}$', value):
        return value

    # Format "5 juin 1805" ou "20 janvier 1862"
    m = re.match(r'^(\d{1,2})\s+(\w+)\s+(\d{4})$', value.lower())
    if m:
        day, month_str, year = m.groups()
        month_normalized = strip_accents(month_str)
        month_num = MOIS_FR.get(month_str) or MOIS_FR.get(month_normalized)
        if month_num:
            return f"{year}-{month_num:02d}-{int(day):02d}"

    # Format "1er janvier 1900"
    m = re.match(r'^1er\s+(\w+)\s+(\d{4})$', value.lower())
    if m:
        month_str, year = m.groups()
        month_num = MOIS_FR.get(month_str) or MOIS_FR.get(strip_accents(month_str))
        if month_num:
            return f"{year}-{month_num:02d}-01"

    return value


def format_list_as_markdown(title, items):
    """Convertit une liste en section markdown."""
    if not items:
        return ""
    lines = [f"\n## {title}\n"]
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                for k, v in item.items():
                    lines.append(f"- **{k}**: {v}")
            else:
                lines.append(f"- {item}")
    elif isinstance(items, str):
        lines.append(items)
    return "\n".join(lines) + "\n"


def name_from_filename(filepath):
    """Extrait un nom complet depuis le nom de fichier."""
    stem = filepath.stem
    name = stem.replace("-", " ")
    return name


def normalize_file(filepath, dry_run=False):
    """
    Normalise une fiche personne vers le schema canonique.
    Retourne True si le fichier a ete modifie.
    """
    try:
        post = frontmatter.load(filepath)
    except Exception as e:
        logger.warning(f"Impossible de lire {filepath}: {e}")
        return False

    meta = dict(post.metadata)
    original_keys = set(meta.keys())
    content = post.content or ""

    # Verifier que c'est bien une fiche Personne
    file_type = str(meta.get("type", "")).strip()
    if file_type and file_type.lower() != "personne":
        return False

    # ---------------------------------------------------------------
    # Etape 1 : Renommage des champs anciens
    # ---------------------------------------------------------------
    for old_key, new_key in FIELD_MAPPING.items():
        if old_key in meta:
            old_val = meta.pop(old_key)
            if new_key and not meta.get(new_key):
                meta[new_key] = old_val

    # ---------------------------------------------------------------
    # Etape 2 : Deplacer les champs riches vers le corps du texte
    # ---------------------------------------------------------------
    extra_body = []
    for field in FIELDS_TO_BODY:
        if field in meta:
            val = meta.pop(field)
            if val:
                section_title = field.replace("_", " ").capitalize()
                section = format_list_as_markdown(section_title, val)
                if section.strip():
                    header_marker = f"## {section_title}"
                    if header_marker not in content:
                        extra_body.append(section)

    # ---------------------------------------------------------------
    # Etape 3 : Supprimer les champs internes inutiles
    # ---------------------------------------------------------------
    for field in FIELDS_TO_DROP:
        meta.pop(field, None)

    # ---------------------------------------------------------------
    # Etape 4 : Normaliser les valeurs
    # ---------------------------------------------------------------
    meta["type"] = "Personne"

    if not meta.get("nom_complet"):
        meta["nom_complet"] = name_from_filename(filepath)

    if meta.get("birth_date"):
        meta["birth_date"] = parse_french_date(str(meta["birth_date"]))
    if meta.get("death_date"):
        meta["death_date"] = parse_french_date(str(meta["death_date"]))

    # Nettoyer les valeurs nulles/vides dans les champs optionnels
    for field in OPTIONAL_FIELDS_PERSON:
        if field in meta and (meta[field] is None or meta[field] == ""):
            del meta[field]

    # S'assurer que tous les champs obligatoires existent
    defaults = {
        "birth_date": None,
        "birth_place": None,
        "nationality": None,
        "genre": None,
        "occupation": None,
        "education": None,
        "summary": "",
        "keywords": [],
        "sources": [],
        "tags": ["elite"],
        "statut_note": "a_valider",
        "date_creation_note": str(date.today()),
    }
    for field, default_val in defaults.items():
        if field not in meta:
            meta[field] = default_val

    # Normaliser les listes
    if isinstance(meta.get("keywords"), str):
        meta["keywords"] = [k.strip() for k in meta["keywords"].split(",") if k.strip()]
    if isinstance(meta.get("sources"), str):
        meta["sources"] = [meta["sources"]]
    if isinstance(meta.get("tags"), str):
        meta["tags"] = [meta["tags"]]

    # Education: toujours une string ou None
    if isinstance(meta.get("education"), list):
        meta["education"] = ", ".join(str(e) for e in meta["education"] if e)
    if meta.get("education") == "":
        meta["education"] = None

    # ---------------------------------------------------------------
    # Etape 5 : Reorganiser les champs dans l'ordre canonique
    # ---------------------------------------------------------------
    ordered = OrderedDict()
    for field in CANONICAL_FIELDS_PERSON:
        if field in meta:
            ordered[field] = meta[field]
    for field in OPTIONAL_FIELDS_PERSON:
        if field in meta and meta[field] is not None and meta[field] != "":
            ordered[field] = meta[field]

    # Verifier s'il y a eu des changements
    new_keys = set(ordered.keys())
    values_changed = any(
        ordered.get(k) != post.metadata.get(k) for k in ordered
    )
    keys_changed = original_keys != new_keys

    if not values_changed and not keys_changed and not extra_body:
        return False

    if dry_run:
        logger.info(f"[DRY RUN] {filepath.name}: {len(original_keys)} -> {len(ordered)} champs")
        return True

    # ---------------------------------------------------------------
    # Etape 6 : Ecrire le fichier normalise
    # ---------------------------------------------------------------
    if extra_body:
        content = content.rstrip() + "\n" + "\n".join(extra_body)

    new_post = frontmatter.Post(content, **ordered)
    with open(filepath, 'wb') as f:
        frontmatter.dump(new_post, f)

    return True


def git_commit_batch(message):
    """Commit les modifications en cours via subprocess."""
    try:
        subprocess.run(["git", "add", "personnes/"], check=True,
                       capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", message],
                check=True, capture_output=True
            )
            logger.info(f"[COMMIT] {message}")
            return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"Erreur git: {e}")
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Normalise les fiches personnes vers un schema uniforme"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche les modifications sans ecrire"
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE_DEFAULT,
        help=f"Nombre de fichiers par lot avant commit intermediaire (defaut: {BATCH_SIZE_DEFAULT})"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Lance les auto-tests integres"
    )
    args = parser.parse_args()

    if args.test:
        run_self_tests()
        return

    if not PERSONNES_DIR.is_dir():
        logger.error(f"Dossier {PERSONNES_DIR} introuvable")
        sys.exit(1)

    files = sorted(PERSONNES_DIR.glob("*.md"))
    total = len(files)
    logger.info(f"Normalisation de {total} fiches (batch_size={args.batch_size}, dry_run={args.dry_run})")

    modified_total = 0
    modified_batch = 0
    batch_num = 0

    for i, filepath in enumerate(files, 1):
        if normalize_file(filepath, dry_run=args.dry_run):
            modified_total += 1
            modified_batch += 1

        # Commit intermediaire
        if not args.dry_run and modified_batch >= args.batch_size:
            batch_num += 1
            msg = f"chore: normalisation fiches lot {batch_num} ({modified_total}/{total} traites)"
            if os.environ.get("GITHUB_ACTIONS"):
                git_commit_batch(msg)
            modified_batch = 0

        if i % 2000 == 0:
            logger.info(f"Progression: {i}/{total} fichiers traites, {modified_total} modifies")

    # Commit final
    if not args.dry_run and modified_batch > 0:
        batch_num += 1
        msg = f"chore: normalisation fiches lot final ({modified_total}/{total} traites)"
        if os.environ.get("GITHUB_ACTIONS"):
            git_commit_batch(msg)

    logger.info(
        f"Termine: {modified_total}/{total} fiches modifiees en {batch_num} lots"
    )


# ---------------------------------------------------------------------------
# Auto-tests integres
# ---------------------------------------------------------------------------
def run_self_tests():
    """Tests integres pour valider la logique de normalisation."""
    import tempfile

    logger.info("Lancement des auto-tests...")

    # Test 1: parse_french_date
    assert parse_french_date("2000-01-15") == "2000-01-15", "ISO date passthrough"
    assert parse_french_date("5 juin 1805") == "1805-06-05", "French date parsing"
    assert parse_french_date("20 janvier 1862") == "1862-01-20", "French date parsing 2"
    assert parse_french_date("1er mars 2000") == "2000-03-01", "1er parsing"
    assert parse_french_date("1990") == "1990", "Year only"
    assert parse_french_date(None) is None, "None passthrough"
    assert parse_french_date("") == "", "Empty passthrough"
    logger.info("[OK] parse_french_date: tous les tests passent")

    # Test 2: normalize_file avec un fichier ancien format
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False,
                                     dir='/tmp') as f:
        f.write("---\n"
                "type: Personne\n"
                "date_naissance: '5 juin 1805'\n"
                "lieu_naissance: Frankfurt\n"
                "nationalite: francaise\n"
                "formation:\n"
                "  - Ecole polytechnique\n"
                "carriere:\n"
                "  - Banquier\n"
                "bio: Une personne importante.\n"
                "keywords:\n"
                "  - finance\n"
                "summary: Test personne\n"
                "prenoms: Betty\n"
                "nom_complet: 'Betty Test'\n"
                "statut: vivant\n"
                "sources:\n"
                "  - https://example.com\n"
                "tags:\n"
                "  - elite\n"
                "---\n"
                "\nContenu du fichier.\n")
        tmp_path = Path(f.name)

    try:
        result = normalize_file(tmp_path)
        assert result is True, "File should be modified"

        post = frontmatter.load(tmp_path)
        meta = post.metadata

        assert meta["type"] == "Personne", f"type: {meta['type']}"
        assert meta["birth_date"] == "1805-06-05", f"birth_date: {meta.get('birth_date')}"
        assert meta["birth_place"] == "Frankfurt", f"birth_place: {meta.get('birth_place')}"
        assert meta["nationality"] == "francaise", f"nationality: {meta.get('nationality')}"
        assert "Ecole polytechnique" in str(meta.get("education", "")), f"education: {meta.get('education')}"
        assert "date_naissance" not in meta, "Old field should be removed"
        assert "lieu_naissance" not in meta, "Old field should be removed"
        assert "nationalite" not in meta, "Old field should be removed"
        assert "formation" not in meta, "Old field should be removed"
        assert "carriere" not in meta, "Should be moved to body"
        assert "bio" not in meta, "Should be moved to body"
        assert "prenoms" not in meta, "Should be dropped"
        assert "statut" not in meta, "Should be dropped"
        assert "Banquier" in post.content, "Career should be in body"
        logger.info("[OK] normalize_file ancien format: tous les tests passent")
    finally:
        tmp_path.unlink()

    # Test 3: normalize_file avec un fichier deja au bon format (pas de modification)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False,
                                     dir='/tmp') as f:
        f.write("---\n"
                "type: Personne\n"
                "nom_complet: Jean Dupont\n"
                "birth_date: '1980-01-01'\n"
                "birth_place: Paris\n"
                "nationality: francaise\n"
                "genre: masculin\n"
                "occupation: ingenieur\n"
                "education: Polytechnique\n"
                "summary: Jean Dupont est ingenieur.\n"
                "keywords:\n"
                "  - ingenieur\n"
                "sources:\n"
                "  - https://example.com\n"
                "tags:\n"
                "  - elite\n"
                "statut_note: a_valider\n"
                "date_creation_note: '2026-01-01'\n"
                "---\n"
                "\nBiographie de Jean Dupont.\n")
        tmp_path = Path(f.name)

    try:
        result = normalize_file(tmp_path)
        # Should not modify (already canonical)
        assert result is False, "Already canonical file should not be modified"
        logger.info("[OK] normalize_file deja canonique: pas de modification")
    finally:
        tmp_path.unlink()

    # Test 4: name_from_filename
    p = Path("/tmp/Jean-Pierre-Dupont.md")
    assert name_from_filename(p) == "Jean Pierre Dupont", f"Got: {name_from_filename(p)}"
    logger.info("[OK] name_from_filename: ok")

    logger.info("[OK] Tous les auto-tests passent")
    return True


if __name__ == "__main__":
    main()

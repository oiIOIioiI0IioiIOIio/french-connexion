"""
Construction d'un index JSON pre-compile pour le site web.

Au lieu de faire 10 000+ appels API GitHub, le site charge un seul fichier
data/index.json qui contient toutes les metadonnees necessaires a l'affichage.
Le contenu complet est charge a la demande quand l'utilisateur ouvre une fiche.

Usage:
    python scripts/09_build_index.py [--output PATH]
    python scripts/09_build_index.py --test
"""

import sys
import os
import json
import re
import argparse
import frontmatter
from pathlib import Path
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger

logger = setup_logger()

ENTITY_DIRS = [
    "personnes",
    "institutions",
    "companies",
    "medias",
    "think tanks",
    "écoles",
    "partis",
]

OUTPUT_DIR = Path("data")
OUTPUT_FILE = OUTPUT_DIR / "index.json"

CONNECTION_RE = re.compile(r'\[\[([^\]]+)\]\]')


def extract_connections(content):
    """Extrait les liens wiki [[...]] du contenu."""
    seen = set()
    connections = []
    for match in CONNECTION_RE.finditer(content):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            connections.append(name)
    return connections


def build_index(output_path=None):
    """Construit l'index JSON de toutes les fiches."""
    if output_path is None:
        output_path = OUTPUT_FILE

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    total_files = 0
    errors = 0

    for dirname in ENTITY_DIRS:
        dirpath = Path(dirname)
        if not dirpath.exists():
            continue

        md_files = sorted(dirpath.glob("*.md"))
        for f in md_files:
            total_files += 1
            try:
                post = frontmatter.load(f)
                meta = post.metadata
                content = post.content or ""
                connections = extract_connections(content)

                entry = {
                    "path": str(f),
                    "name": meta.get("nom_complet") or f.stem.replace("-", " "),
                    "type": meta.get("type", "Document"),
                    "summary": meta.get("summary", "")[:300],
                    "occupation": meta.get("occupation") or meta.get("industry") or None,
                    "birth_date": meta.get("birth_date") or meta.get("founded") or None,
                    "birth_place": meta.get("birth_place") or meta.get("headquarters") or None,
                    "nationality": meta.get("nationality") or None,
                    "keywords": (meta.get("keywords") or [])[:8],
                    "connections": connections[:20],
                    "tags": meta.get("tags") or [],
                }

                # Retirer les valeurs None pour reduire la taille
                entry = {k: v for k, v in entry.items() if v is not None}

                entries.append(entry)

            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"[WARN] Erreur lecture {f}: {e}")

    # Trier par nom
    entries.sort(key=lambda e: e.get("name", "").lower())

    index_data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(entries),
        "files": entries,
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, ensure_ascii=False, separators=(',', ':'))

    size_kb = output_path.stat().st_size / 1024
    logger.info(f"Index genere: {len(entries)} fiches, {size_kb:.0f} KB -> {output_path}")
    if errors:
        logger.warning(f"  {errors} erreurs de lecture ignorees")

    return len(entries)


def main():
    parser = argparse.ArgumentParser(
        description="Construit l'index JSON pour le site web"
    )
    parser.add_argument("--output", default=str(OUTPUT_FILE),
                        help="Chemin du fichier de sortie")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        run_self_tests()
        return

    count = build_index(args.output)
    logger.info(f"Termine: {count} fiches indexees")


def run_self_tests():
    """Auto-tests pour valider les fonctions."""
    logger.info("Lancement des auto-tests...")

    # Test extract_connections
    assert extract_connections("Lien vers [[Jacques Chirac]] et [[Nicolas Sarkozy]].") == \
        ["Jacques Chirac", "Nicolas Sarkozy"]
    assert extract_connections("Pas de lien ici.") == []
    assert extract_connections("[[A]] et [[A]] doublon") == ["A"]
    logger.info("[OK] extract_connections: ok")

    # Test build_index sur le repo reel (si disponible)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False, dir='/tmp') as f:
        tmp_path = Path(f.name)

    try:
        count = build_index(tmp_path)
        assert count > 0, f"Should index files, got {count}"

        with open(tmp_path, 'r') as f:
            data = json.load(f)
        assert "total" in data
        assert "files" in data
        assert data["total"] == len(data["files"])
        assert data["total"] == count
        logger.info(f"[OK] build_index: {count} fiches indexees")
    finally:
        tmp_path.unlink()

    logger.info("[OK] Tous les auto-tests passent")


if __name__ == "__main__":
    main()

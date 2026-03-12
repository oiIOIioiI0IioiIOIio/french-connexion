"""
Import d'institutions depuis des sources officielles ouvertes.

Ce script importe des fiches pour :
- Partis politiques (via data.gouv.fr / RNE)
- Grandes ecoles et universites (via data.gouv.fr / Wikidata)
- Think tanks et fondations (via Journal Officiel / Wikidata)
- Grandes entreprises (via Wikidata / registre du commerce)

Toutes les sources sont publiques et les donnees ouvertes.
Conforme a l'ethique journalistique : que des faits, pas d'opinions.

Usage:
    python scripts/10_import_institutions.py [--source SOURCE] [--dry-run]
    python scripts/10_import_institutions.py --test
"""

import sys
import os
import re
import json
import time
import argparse
import unicodedata
import frontmatter
from pathlib import Path
from datetime import date, datetime
from collections import OrderedDict
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger

logger = setup_logger()

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Repertoires cibles
DIRS = {
    "parti": Path("partis"),
    "ecole": Path("écoles"),
    "think_tank": Path("think tanks"),
    "entreprise": Path("companies"),
    "institution": Path("institutions"),
    "media": Path("medias"),
}


def http_get_json(url, retries=2):
    """Requete HTTP GET avec retries. Retourne le JSON parse ou None."""
    headers = {
        "User-Agent": "FrenchConnexion/1.0 (research project; https://github.com/oiIOIioiI0IioiIOIio/french-connexion)",
        "Accept": "application/json",
    }
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, json.JSONDecodeError) as e:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
            else:
                logger.warning(f"[WARN] HTTP error {url}: {e}")
    return None


def slugify(name):
    """Cree un nom de fichier propre a partir d'un nom."""
    name = name.strip()
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'[/\\:*?"<>|]', '', name)
    name = re.sub(r'\s+', '-', name)
    return name


def file_exists(directory, name):
    """Verifie si un fichier existe deja (insensible a la casse)."""
    slug = slugify(name)
    target = directory / f"{slug}.md"
    if target.exists():
        return True
    # Verification insensible a la casse
    name_lower = slug.lower()
    for f in directory.glob("*.md"):
        if f.stem.lower() == name_lower:
            return True
    return False


def write_fiche(directory, name, metadata, body=""):
    """Ecrit une fiche markdown avec frontmatter."""
    slug = slugify(name)
    target = directory / f"{slug}.md"
    if target.exists():
        return False

    directory.mkdir(parents=True, exist_ok=True)

    post = frontmatter.Post(body, **metadata)
    with open(target, 'wb') as f:
        frontmatter.dump(post, f)
    return True


# ---------------------------------------------------------------------------
# Source 1 : Partis politiques depuis Wikidata
# ---------------------------------------------------------------------------
def fetch_partis_wikidata():
    """
    Recupere les partis politiques francais depuis Wikidata SPARQL.
    Source ouverte, donnees factuelles.
    """
    logger.info("Import des partis politiques depuis Wikidata...")

    query = """
    SELECT DISTINCT ?party ?partyLabel ?founded ?hqLabel ?leaderLabel ?ideology ?ideologyLabel ?website WHERE {
      ?party wdt:P31/wdt:P279* wd:Q7278.
      ?party wdt:P17 wd:Q142.
      OPTIONAL { ?party wdt:P571 ?founded. }
      OPTIONAL { ?party wdt:P159 ?hq. }
      OPTIONAL { ?party wdt:P6 ?leader. }
      OPTIONAL { ?party wdt:P1142 ?ideology. }
      OPTIONAL { ?party wdt:P856 ?website. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
    }
    ORDER BY ?partyLabel
    LIMIT 500
    """

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    data = http_get_json(url)
    if not data:
        logger.warning("[WARN] Echec requete Wikidata partis")
        return []

    results = []
    seen = set()
    for item in data.get("results", {}).get("bindings", []):
        name = item.get("partyLabel", {}).get("value", "").strip()
        if not name or name in seen or name.startswith("Q"):
            continue
        seen.add(name)

        qid = item.get("party", {}).get("value", "").split("/")[-1]
        founded_raw = item.get("founded", {}).get("value", "")
        founded = founded_raw[:10] if founded_raw else None

        meta = OrderedDict([
            ("type", "Parti"),
            ("nom_complet", name),
            ("founded", founded),
            ("headquarters", item.get("hqLabel", {}).get("value") or None),
            ("leader", item.get("leaderLabel", {}).get("value") or None),
            ("ideology", item.get("ideologyLabel", {}).get("value") or None),
            ("website", item.get("website", {}).get("value") or None),
            ("summary", f"{name}, parti politique francais."),
            ("keywords", ["parti politique", "politique"]),
            ("sources", [f"https://www.wikidata.org/entity/{qid}"]),
            ("tags", ["institution", "source-wikidata"]),
            ("wikidata_id", qid),
            ("statut_note", "a_valider"),
            ("date_creation_note", str(date.today())),
        ])
        # Nettoyer les None
        meta = OrderedDict((k, v) for k, v in meta.items() if v is not None)

        results.append({"name": name, "meta": meta})

    logger.info(f"  {len(results)} partis trouves")
    return results


# ---------------------------------------------------------------------------
# Source 2 : Grandes ecoles depuis Wikidata
# ---------------------------------------------------------------------------
def fetch_ecoles_wikidata():
    """
    Recupere les grandes ecoles francaises depuis Wikidata SPARQL.
    Inclut les universites, ecoles d'ingenieur, ecoles de commerce.
    """
    logger.info("Import des grandes ecoles depuis Wikidata...")

    query = """
    SELECT DISTINCT ?school ?schoolLabel ?founded ?hqLabel ?website WHERE {
      {
        ?school wdt:P31/wdt:P279* wd:Q3918.
        ?school wdt:P17 wd:Q142.
      } UNION {
        ?school wdt:P31/wdt:P279* wd:Q38723.
        ?school wdt:P17 wd:Q142.
      } UNION {
        ?school wdt:P31/wdt:P279* wd:Q189004.
        ?school wdt:P17 wd:Q142.
      }
      OPTIONAL { ?school wdt:P571 ?founded. }
      OPTIONAL { ?school wdt:P159 ?hq. }
      OPTIONAL { ?school wdt:P856 ?website. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
    }
    ORDER BY ?schoolLabel
    LIMIT 800
    """

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    data = http_get_json(url)
    if not data:
        logger.warning("[WARN] Echec requete Wikidata ecoles")
        return []

    results = []
    seen = set()
    for item in data.get("results", {}).get("bindings", []):
        name = item.get("schoolLabel", {}).get("value", "").strip()
        if not name or name in seen or name.startswith("Q"):
            continue
        seen.add(name)

        qid = item.get("school", {}).get("value", "").split("/")[-1]
        founded_raw = item.get("founded", {}).get("value", "")
        founded = founded_raw[:10] if founded_raw else None

        meta = OrderedDict([
            ("type", "Ecole"),
            ("nom_complet", name),
            ("founded", founded),
            ("headquarters", item.get("hqLabel", {}).get("value") or None),
            ("website", item.get("website", {}).get("value") or None),
            ("summary", f"{name}, etablissement d'enseignement superieur francais."),
            ("keywords", ["enseignement superieur", "grande ecole"]),
            ("sources", [f"https://www.wikidata.org/entity/{qid}"]),
            ("tags", ["institution", "source-wikidata"]),
            ("wikidata_id", qid),
            ("statut_note", "a_valider"),
            ("date_creation_note", str(date.today())),
        ])
        meta = OrderedDict((k, v) for k, v in meta.items() if v is not None)
        results.append({"name": name, "meta": meta})

    logger.info(f"  {len(results)} ecoles trouvees")
    return results


# ---------------------------------------------------------------------------
# Source 3 : Think tanks et fondations depuis Wikidata
# ---------------------------------------------------------------------------
def fetch_think_tanks_wikidata():
    """Recupere les think tanks et fondations francais depuis Wikidata."""
    logger.info("Import des think tanks et fondations depuis Wikidata...")

    query = """
    SELECT DISTINCT ?org ?orgLabel ?founded ?hqLabel ?website WHERE {
      {
        ?org wdt:P31/wdt:P279* wd:Q170584.
        ?org wdt:P17 wd:Q142.
      } UNION {
        ?org wdt:P31/wdt:P279* wd:Q157031.
        ?org wdt:P17 wd:Q142.
      }
      OPTIONAL { ?org wdt:P571 ?founded. }
      OPTIONAL { ?org wdt:P159 ?hq. }
      OPTIONAL { ?org wdt:P856 ?website. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
    }
    ORDER BY ?orgLabel
    LIMIT 500
    """

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    data = http_get_json(url)
    if not data:
        logger.warning("[WARN] Echec requete Wikidata think tanks")
        return []

    results = []
    seen = set()
    for item in data.get("results", {}).get("bindings", []):
        name = item.get("orgLabel", {}).get("value", "").strip()
        if not name or name in seen or name.startswith("Q"):
            continue
        seen.add(name)

        qid = item.get("org", {}).get("value", "").split("/")[-1]
        founded_raw = item.get("founded", {}).get("value", "")
        founded = founded_raw[:10] if founded_raw else None

        meta = OrderedDict([
            ("type", "Fondation"),
            ("nom_complet", name),
            ("founded", founded),
            ("headquarters", item.get("hqLabel", {}).get("value") or None),
            ("website", item.get("website", {}).get("value") or None),
            ("summary", f"{name}, think tank ou fondation."),
            ("keywords", ["think tank", "fondation", "influence"]),
            ("sources", [f"https://www.wikidata.org/entity/{qid}"]),
            ("tags", ["institution", "source-wikidata"]),
            ("wikidata_id", qid),
            ("statut_note", "a_valider"),
            ("date_creation_note", str(date.today())),
        ])
        meta = OrderedDict((k, v) for k, v in meta.items() if v is not None)
        results.append({"name": name, "meta": meta})

    logger.info(f"  {len(results)} think tanks / fondations trouves")
    return results


# ---------------------------------------------------------------------------
# Source 4 : Grandes entreprises francaises (CAC 40 + SBF 120) depuis Wikidata
# ---------------------------------------------------------------------------
def fetch_entreprises_wikidata():
    """Recupere les grandes entreprises francaises depuis Wikidata."""
    logger.info("Import des grandes entreprises depuis Wikidata...")

    query = """
    SELECT DISTINCT ?company ?companyLabel ?founded ?hqLabel ?ceoLabel ?industryLabel ?website WHERE {
      {
        ?company wdt:P361 wd:Q185729.
      } UNION {
        ?company wdt:P361 wd:Q744465.
      } UNION {
        ?company wdt:P17 wd:Q142.
        ?company wdt:P31/wdt:P279* wd:Q4830453.
        ?company wdt:P414 ?stockExchange.
      }
      OPTIONAL { ?company wdt:P571 ?founded. }
      OPTIONAL { ?company wdt:P159 ?hq. }
      OPTIONAL { ?company wdt:P169 ?ceo. }
      OPTIONAL { ?company wdt:P452 ?industry. }
      OPTIONAL { ?company wdt:P856 ?website. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
    }
    ORDER BY ?companyLabel
    LIMIT 500
    """

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    data = http_get_json(url)
    if not data:
        logger.warning("[WARN] Echec requete Wikidata entreprises")
        return []

    results = []
    seen = set()
    for item in data.get("results", {}).get("bindings", []):
        name = item.get("companyLabel", {}).get("value", "").strip()
        if not name or name in seen or name.startswith("Q"):
            continue
        seen.add(name)

        qid = item.get("company", {}).get("value", "").split("/")[-1]
        founded_raw = item.get("founded", {}).get("value", "")
        founded = founded_raw[:10] if founded_raw else None
        industry = item.get("industryLabel", {}).get("value") or None

        meta = OrderedDict([
            ("type", "Entreprise"),
            ("nom_complet", name),
            ("founded", founded),
            ("headquarters", item.get("hqLabel", {}).get("value") or None),
            ("leader", item.get("ceoLabel", {}).get("value") or None),
            ("industry", industry),
            ("website", item.get("website", {}).get("value") or None),
            ("summary", f"{name}, entreprise francaise."),
            ("keywords", ["entreprise", industry or "business"]),
            ("sources", [f"https://www.wikidata.org/entity/{qid}"]),
            ("tags", ["entreprise", "source-wikidata"]),
            ("wikidata_id", qid),
            ("statut_note", "a_valider"),
            ("date_creation_note", str(date.today())),
        ])
        meta = OrderedDict((k, v) for k, v in meta.items() if v is not None)
        results.append({"name": name, "meta": meta})

    logger.info(f"  {len(results)} entreprises trouvees")
    return results


# ---------------------------------------------------------------------------
# Source 5 : Medias francais depuis Wikidata
# ---------------------------------------------------------------------------
def fetch_medias_wikidata():
    """Recupere les principaux medias francais depuis Wikidata."""
    logger.info("Import des medias depuis Wikidata...")

    query = """
    SELECT DISTINCT ?media ?mediaLabel ?founded ?website ?ownerLabel WHERE {
      {
        ?media wdt:P31/wdt:P279* wd:Q11032.
        ?media wdt:P17 wd:Q142.
      } UNION {
        ?media wdt:P31/wdt:P279* wd:Q1110794.
        ?media wdt:P17 wd:Q142.
      } UNION {
        ?media wdt:P31/wdt:P279* wd:Q1153191.
        ?media wdt:P495 wd:Q142.
      }
      OPTIONAL { ?media wdt:P571 ?founded. }
      OPTIONAL { ?media wdt:P856 ?website. }
      OPTIONAL { ?media wdt:P127 ?owner. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
    }
    ORDER BY ?mediaLabel
    LIMIT 500
    """

    url = "https://query.wikidata.org/sparql?format=json&query=" + quote(query)
    data = http_get_json(url)
    if not data:
        logger.warning("[WARN] Echec requete Wikidata medias")
        return []

    results = []
    seen = set()
    for item in data.get("results", {}).get("bindings", []):
        name = item.get("mediaLabel", {}).get("value", "").strip()
        if not name or name in seen or name.startswith("Q"):
            continue
        seen.add(name)

        qid = item.get("media", {}).get("value", "").split("/")[-1]
        founded_raw = item.get("founded", {}).get("value", "")
        founded = founded_raw[:10] if founded_raw else None
        owner = item.get("ownerLabel", {}).get("value") or None

        meta = OrderedDict([
            ("type", "Media"),
            ("nom_complet", name),
            ("founded", founded),
            ("owner", owner),
            ("website", item.get("website", {}).get("value") or None),
            ("summary", f"{name}, media francais."),
            ("keywords", ["media", "presse"]),
            ("sources", [f"https://www.wikidata.org/entity/{qid}"]),
            ("tags", ["media", "source-wikidata"]),
            ("wikidata_id", qid),
            ("statut_note", "a_valider"),
            ("date_creation_note", str(date.today())),
        ])
        meta = OrderedDict((k, v) for k, v in meta.items() if v is not None)
        results.append({"name": name, "meta": meta})

    logger.info(f"  {len(results)} medias trouves")
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
SOURCES = {
    "partis": (fetch_partis_wikidata, DIRS["parti"]),
    "ecoles": (fetch_ecoles_wikidata, DIRS["ecole"]),
    "think_tanks": (fetch_think_tanks_wikidata, DIRS["think_tank"]),
    "entreprises": (fetch_entreprises_wikidata, DIRS["entreprise"]),
    "medias": (fetch_medias_wikidata, DIRS["media"]),
}


def import_source(source_name, dry_run=False):
    """Importe les fiches d'une source et les ecrit sur disque."""
    if source_name not in SOURCES:
        logger.error(f"Source inconnue: {source_name}. Disponibles: {list(SOURCES.keys())}")
        return 0

    fetch_fn, target_dir = SOURCES[source_name]
    target_dir.mkdir(parents=True, exist_ok=True)

    items = fetch_fn()
    created = 0
    skipped = 0

    for item in items:
        name = item["name"]
        if file_exists(target_dir, name):
            skipped += 1
            continue

        if dry_run:
            logger.info(f"  [DRY RUN] Creerait: {target_dir}/{slugify(name)}.md")
            created += 1
            continue

        body = item["meta"].get("summary", "")
        if write_fiche(target_dir, name, item["meta"], body):
            created += 1

    logger.info(f"  {source_name}: {created} crees, {skipped} existants")
    return created


def main():
    parser = argparse.ArgumentParser(
        description="Import d'institutions depuis des sources officielles"
    )
    parser.add_argument(
        "--source", default="all",
        choices=["all"] + list(SOURCES.keys()),
        help="Source a importer (defaut: all)"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        run_self_tests()
        return

    dry_run = args.dry_run or DRY_RUN

    if args.source == "all":
        sources_to_run = list(SOURCES.keys())
    else:
        sources_to_run = [args.source]

    total_created = 0
    for source in sources_to_run:
        total_created += import_source(source, dry_run=dry_run)

    logger.info(f"Import termine: {total_created} fiches creees au total")

    if total_created > 0 and not dry_run and not os.environ.get("GITHUB_ACTIONS"):
        import subprocess
        try:
            for d in DIRS.values():
                if d.exists():
                    subprocess.run(["git", "add", str(d)], capture_output=True)
            subprocess.run(
                ["git", "commit", "-m",
                 f"feat: import {total_created} institutions depuis sources officielles"],
                capture_output=True
            )
            logger.info("[OK] Commit effectue")
        except Exception as e:
            logger.warning(f"Erreur git: {e}")


def run_self_tests():
    """Auto-tests pour valider les fonctions utilitaires."""
    logger.info("Lancement des auto-tests...")

    # Test slugify
    assert slugify("École Nationale d'Administration") == "École-Nationale-d'Administration"
    assert slugify("Parti  Socialiste") == "Parti-Socialiste"
    logger.info("[OK] slugify: ok")

    # Test file_exists
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        p = Path(tmpdir)
        (p / "Test-Entity.md").write_text("test")
        assert file_exists(p, "Test Entity") is True
        assert file_exists(p, "Nonexistent") is False
    logger.info("[OK] file_exists: ok")

    logger.info("[OK] Tous les auto-tests passent")


if __name__ == "__main__":
    main()

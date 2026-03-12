"""
Aggregation de donnees biographiques sur les elites francaises
a partir de sources publiques officielles.

Interroge des APIs publiques et ouvertes pour collecter des profils
de personnalites francaises. Le script cree des fiches personne au
format standard du depot (YAML frontmatter + Markdown), compatible
avec le site web (index.html) et le reste du pipeline.

SOURCES PUBLIQUES UTILISEES:
1. Wikidata SPARQL  - donnees biographiques structurees (CC0)
2. Assemblee Nationale open data - deputes (licence ouverte)
3. Senat open data - senateurs (licence ouverte)
4. HATVP CSV index - declarations d'interets (transparence publique)
   Inspire de https://github.com/oiIOIioiI0IioiIOIio/transparence-nationale
5. Repertoire National des Elus (RNE) via API tabulaire data.gouv.fr
   https://tabular-api.data.gouv.fr/api
   Documentation : https://tabular-api.data.gouv.fr/api/doc
   Donnees officielles du Ministere de l'Interieur (licence ouverte)

VARIABLES D'ENVIRONNEMENT:
- GITHUB_ACTIONS : Detecte automatiquement (ajuste les limites)
- MAX_RESULTS : Nombre max de resultats par source (defaut: 2000)
- HTTP_TIMEOUT : Timeout requetes HTTP en secondes (defaut: 30)
- DRY_RUN : Si '1', affiche sans creer de fichiers
- HTTP_MAX_RETRIES : Nombre de tentatives HTTP (defaut: 3)

UTILISATION:
  python scripts/07_aggregate_public_sources.py
  python scripts/07_aggregate_public_sources.py --test
  python scripts/07_aggregate_public_sources.py --source wikidata
  python scripts/07_aggregate_public_sources.py --source assemblee
  python scripts/07_aggregate_public_sources.py --source senat
  python scripts/07_aggregate_public_sources.py --source rne
  DRY_RUN=1 python scripts/07_aggregate_public_sources.py
"""

import sys
import os
import csv
import io
import json
import re
import time
import frontmatter
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict
from urllib.request import urlopen, Request
from urllib.parse import quote, urlencode
from urllib.error import URLError, HTTPError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

# Configuration
load_dotenv()
logger = setup_logger()
git = GitHandler()

# Detection de l'environnement GitHub Actions
IS_GITHUB_ACTION = os.getenv('GITHUB_ACTIONS') == 'true'

# Limites configurables
MAX_RESULTS = int(os.getenv('MAX_RESULTS', '1000' if IS_GITHUB_ACTION else '200'))
HTTP_TIMEOUT = int(os.getenv('HTTP_TIMEOUT', '30'))
DRY_RUN = os.getenv('DRY_RUN', '0') == '1'
INTER_REQUEST_DELAY = float(os.getenv('INTER_REQUEST_DELAY', '1.0'))
HTTP_MAX_RETRIES = int(os.getenv('HTTP_MAX_RETRIES', '3'))

# Statistiques globales
STATS = defaultdict(int)

# User-Agent commun
USER_AGENT = "FrenchConnexion/1.0 (https://github.com/french-connexion; public data aggregation)"


# ============================================================
# HTTP helpers
# ============================================================

def _http_get(url: str, headers: Optional[dict] = None,
              timeout: Optional[int] = None,
              retries: int = 0) -> Optional[bytes]:
    """Effectue une requete HTTP GET avec gestion d'erreurs robuste.

    Si retries > 0, reessaye avec backoff exponentiel en cas d'erreur
    reseau ou de timeout.
    """
    if headers is None:
        headers = {}
    headers.setdefault("User-Agent", USER_AGENT)
    if timeout is None:
        timeout = HTTP_TIMEOUT

    max_attempts = 1 + retries
    for attempt in range(max_attempts):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except HTTPError as e:
            logger.warning(f"HTTP {e.code} pour {url}")
            STATS['http_errors'] += 1
            return None
        except (URLError, Exception) as e:
            is_timeout = "timed out" in str(e) or "timeout" in str(e).lower()
            if attempt < max_attempts - 1 and is_timeout:
                wait = 2 ** attempt * 2
                logger.info(
                    f"[RETRY] Tentative {attempt + 2}/{max_attempts} "
                    f"dans {wait}s pour {url}"
                )
                time.sleep(wait)
                continue
            if isinstance(e, URLError):
                logger.warning(f"Erreur reseau pour {url}: {e.reason}")
                STATS['network_errors'] += 1
            else:
                logger.warning(f"Erreur inattendue pour {url}: {e}")
                STATS['other_errors'] += 1
            return None
    return None


def _http_get_json(url: str, headers: Optional[dict] = None,
                   timeout: Optional[int] = None,
                   retries: int = 0) -> Optional[dict]:
    """Effectue une requete HTTP GET et parse le JSON."""
    raw = _http_get(url, headers=headers, timeout=timeout, retries=retries)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.warning(f"Erreur parsing JSON: {e}")
        return None


# ============================================================
# Existing file index
# ============================================================

def build_existing_index() -> Set[str]:
    """Construit un index des fiches personne existantes.

    Retourne un ensemble de noms normalises pour eviter les doublons.
    """
    existing = set()
    folders = ["personnes", "institutions", "companies", "medias",
               "think tanks", "\u00e9coles"]

    for folder_name in folders:
        folder = Path(folder_name)
        if not folder.exists():
            continue
        for md_file in folder.rglob("*.md"):
            name = md_file.stem.replace('-', ' ').strip().lower()
            existing.add(name)

    logger.info(f"Index existant : {len(existing)} fiches")
    return existing


def _normalize_name(name: str) -> str:
    """Normalise un nom pour comparaison (minuscules, sans accents speciaux)."""
    return name.strip().lower().replace('-', ' ')


def _name_already_exists(name: str, existing_index: Set[str]) -> bool:
    """Verifie si un nom existe deja dans l'index."""
    normalized = _normalize_name(name)
    if normalized in existing_index:
        return True
    # Verification partielle pour les noms composes
    parts = normalized.split()
    if len(parts) >= 2:
        # Essayer prenom nom seulement
        short = f"{parts[0]} {parts[-1]}"
        if short in existing_index:
            return True
    return False


# ============================================================
# Source 1: Wikidata SPARQL
# ============================================================

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# Categories de personnalites francaises a rechercher
# Chaque categorie = (label, occupation_id, description)
WIKIDATA_CATEGORIES = [
    ("politiciens", "Q82955", "homme/femme politique"),
    ("chefs_entreprise", "Q484876", "dirigeant d'entreprise"),
    ("hauts_fonctionnaires", "Q599151", "haut fonctionnaire"),
    ("diplomates", "Q193391", "diplomate"),
    ("journalistes", "Q1930187", "journaliste"),
    ("magistrats", "Q16533", "magistrat"),
    ("universitaires", "Q1622272", "universitaire"),
    ("banquiers", "Q806798", "banquier"),
    ("avocats", "Q40348", "avocat"),
    ("militaires_haut_grade", "Q47064", "militaire"),
]


def _build_sparql_query(occupation_id: str, limit: int) -> str:
    """Construit une requete SPARQL pour Wikidata.

    Recherche des personnes francaises ayant une occupation donnee,
    nees apres 1920 (pertinence contemporaine).
    """
    return f"""
    SELECT DISTINCT
        ?person ?personLabel ?birthDate ?birthPlaceLabel
        ?deathDate ?genderLabel ?occupationLabel ?educationLabel
    WHERE {{
        ?person wdt:P31 wd:Q5 .
        ?person wdt:P27 wd:Q142 .
        ?person wdt:P106 wd:{occupation_id} .
        ?person wdt:P569 ?birthDate .
        FILTER(YEAR(?birthDate) > 1920)
        OPTIONAL {{ ?person wdt:P19 ?birthPlace . }}
        OPTIONAL {{ ?person wdt:P570 ?deathDate . }}
        OPTIONAL {{ ?person wdt:P21 ?gender . }}
        OPTIONAL {{ ?person wdt:P106 ?occupation . }}
        OPTIONAL {{ ?person wdt:P69 ?education . }}
        SERVICE wikibase:label {{
            bd:serviceParam wikibase:language "fr,en" .
        }}
    }}
    ORDER BY DESC(?birthDate)
    LIMIT {limit}
    """


def fetch_wikidata_category(category_label: str, occupation_id: str,
                            limit: int) -> List[dict]:
    """Interroge Wikidata SPARQL pour une categorie de personnalites.

    Retourne une liste de dictionnaires avec les donnees biographiques.
    """
    query = _build_sparql_query(occupation_id, limit)
    params = urlencode({"query": query, "format": "json"})
    url = f"{WIKIDATA_SPARQL_ENDPOINT}?{params}"

    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": USER_AGENT,
    }

    data = _http_get_json(url, headers=headers, timeout=45, retries=1)
    if data is None:
        logger.warning(f"[FAIL] Wikidata: pas de resultats pour {category_label}")
        return []

    results = data.get("results", {}).get("bindings", [])
    persons = []

    for row in results:
        name = row.get("personLabel", {}).get("value", "")
        if not name or name.startswith("Q"):
            continue

        person = {
            "nom_complet": name,
            "source": "wikidata_sparql",
            "source_category": category_label,
            "wikidata_url": row.get("person", {}).get("value", ""),
        }

        birth = row.get("birthDate", {}).get("value", "")
        if birth:
            person["date_naissance"] = birth.split("T")[0]

        death = row.get("deathDate", {}).get("value", "")
        if death:
            person["date_deces"] = death.split("T")[0]

        place = row.get("birthPlaceLabel", {}).get("value", "")
        if place and not place.startswith("Q"):
            person["lieu_naissance"] = place

        gender = row.get("genderLabel", {}).get("value", "")
        if gender and not gender.startswith("Q"):
            person["genre"] = gender

        occupation = row.get("occupationLabel", {}).get("value", "")
        if occupation and not occupation.startswith("Q"):
            person["occupation"] = occupation

        education = row.get("educationLabel", {}).get("value", "")
        if education and not education.startswith("Q"):
            person["formation"] = education

        persons.append(person)

    logger.info(f"[OK] Wikidata {category_label}: {len(persons)} personnes")
    STATS['wikidata_results'] += len(persons)
    return persons


def fetch_all_wikidata(limit_per_category: int = 50) -> List[dict]:
    """Interroge toutes les categories Wikidata.

    Retourne la liste fusionnee de toutes les personnalites trouvees.
    """
    all_persons = []
    seen_urls = set()

    for cat_label, occ_id, _desc in WIKIDATA_CATEGORIES:
        logger.info(f"Wikidata: recherche {cat_label}...")
        persons = fetch_wikidata_category(cat_label, occ_id, limit_per_category)

        for p in persons:
            url = p.get("wikidata_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_persons.append(p)

        if persons:
            time.sleep(INTER_REQUEST_DELAY)

    logger.info(f"[OK] Wikidata total: {len(all_persons)} personnes uniques")
    return all_persons


# ============================================================
# Source 2: Assemblee Nationale (deputes)
# ============================================================

# L'Assemblee Nationale fournit des donnees ouvertes via
# https://data.assemblee-nationale.fr
# Format: JSON avec listes de deputes et mandats
AN_BASE_URL = "https://data.assemblee-nationale.fr"
# API NosDonnees.fr - donnees parlementaires ouvertes au format JSON
AN_API_URL = "https://www.nosdeputes.fr/deputes/json"
AN_API_ENMANDAT_URL = "https://www.nosdeputes.fr/deputes/enmandat/json"


def _parse_an_response(data) -> List[dict]:
    """Parse la reponse de l'API Assemblee Nationale quel que soit le format.

    Gere differentes structures de reponse :
    - {"deputes": [{"depute": {...}}, ...]}  (format historique)
    - [{"depute": {...}}, ...]               (format liste directe)
    - {"deputes": [{"id": ..., "nom": ...}]} (format sans wrapper depute)
    """
    if data is None:
        return []

    deputes_list = []

    if isinstance(data, dict):
        # Format historique : {"deputes": [...]}
        deputes_list = data.get("deputes", [])
        # Essayer aussi des cles alternatives
        if not deputes_list:
            deputes_list = data.get("results", [])
        if not deputes_list:
            deputes_list = data.get("data", [])
    elif isinstance(data, list):
        # Format liste directe
        deputes_list = data

    return deputes_list


def fetch_assemblee_deputes() -> List[dict]:
    """Recupere la liste des deputes depuis NosDonnees.fr (API ouverte).

    NosDonnees.fr (Regards Citoyens) est un projet citoyen qui agrege
    les donnees publiques de l'Assemblee Nationale sous licence ouverte.
    Essaye plusieurs URLs en fallback.
    """
    persons = []

    # Essayer d'abord les deputes en mandat
    data = _http_get_json(AN_API_ENMANDAT_URL, timeout=30, retries=1)
    deputes_list = _parse_an_response(data)

    if not deputes_list:
        # Fallback : tous les deputes
        data = _http_get_json(AN_API_URL, timeout=30, retries=1)
        deputes_list = _parse_an_response(data)

    if not deputes_list:
        if data is None:
            logger.warning("[FAIL] Assemblee Nationale: API inaccessible")
        else:
            logger.warning(
                "[WARN] Assemblee Nationale: aucun depute dans la reponse"
            )
        return []

    for entry in deputes_list:
        # Gerer les deux formats de reponse :
        # {"depute": {...}} (format avec wrapper) ou {...} (format direct)
        if isinstance(entry, dict) and "depute" in entry:
            dep = entry.get("depute", {})
        elif isinstance(entry, dict):
            dep = entry
        else:
            continue
        if not dep:
            continue

        nom = dep.get("nom", "").strip()
        if not nom:
            continue

        person = {
            "nom_complet": nom,
            "source": "assemblee_nationale",
            "source_category": "depute",
            "occupation": "depute",
            "slug_an": dep.get("slug", ""),
        }

        # Donnees biographiques
        birth = dep.get("date_naissance", "")
        if birth:
            person["date_naissance"] = birth

        lieu = dep.get("lieu_naissance", "")
        if lieu:
            person["lieu_naissance"] = lieu

        sexe = dep.get("sexe", "")
        if sexe:
            person["genre"] = "masculin" if sexe == "H" else "feminin"

        # Groupe politique
        groupe = dep.get("groupe_sigle", "")
        if groupe:
            person["groupe_politique"] = groupe

        parti = dep.get("parti_ratt_financier", "")
        if parti:
            person["parti"] = parti

        profession = dep.get("profession", "")
        if profession:
            person["profession_origine"] = profession

        # URL de la fiche
        slug = dep.get("slug", "")
        if slug:
            person["url_nosdeputes"] = f"https://www.nosdeputes.fr/{slug}"

        # Photo
        photo_url = dep.get("photo_url", "")
        if photo_url:
            person["photo_url"] = photo_url

        persons.append(person)

    logger.info(f"[OK] Assemblee Nationale: {len(persons)} deputes")
    STATS['assemblee_results'] += len(persons)
    return persons


# ============================================================
# Source 3: Senat (senateurs)
# ============================================================

# Le Senat fournit des donnees ouvertes via data.senat.fr
# NosSenateurs.fr agrege ces donnees en JSON accessible
SENAT_API_URL = "https://www.nossenateurs.fr/senateurs/json"
SENAT_API_ENMANDAT_URL = "https://www.nossenateurs.fr/senateurs/enmandat/json"


def _parse_senat_response(data) -> List[dict]:
    """Parse la reponse de l'API Senat quel que soit le format.

    Gere differentes structures de reponse :
    - {"senateurs": [{"senateur": {...}}, ...]}  (format historique)
    - [{"senateur": {...}}, ...]                 (format liste directe)
    - {"senateurs": [{"id": ..., "nom": ...}]}  (format sans wrapper)
    """
    if data is None:
        return []

    senateurs_list = []

    if isinstance(data, dict):
        senateurs_list = data.get("senateurs", [])
        if not senateurs_list:
            senateurs_list = data.get("results", [])
        if not senateurs_list:
            senateurs_list = data.get("data", [])
    elif isinstance(data, list):
        senateurs_list = data

    return senateurs_list


def fetch_senat_senateurs() -> List[dict]:
    """Recupere la liste des senateurs depuis NosSenateurs.fr (API ouverte).

    NosSenateurs.fr (Regards Citoyens) agrege les donnees publiques
    du Senat sous licence ouverte.
    Essaye plusieurs URLs en fallback.
    """
    persons = []

    # Essayer d'abord les senateurs en mandat
    data = _http_get_json(SENAT_API_ENMANDAT_URL, timeout=30, retries=1)
    senateurs_list = _parse_senat_response(data)

    if not senateurs_list:
        data = _http_get_json(SENAT_API_URL, timeout=30, retries=1)
        senateurs_list = _parse_senat_response(data)

    if not senateurs_list:
        if data is None:
            logger.warning("[FAIL] Senat: API inaccessible")
        else:
            logger.warning("[WARN] Senat: aucun senateur dans la reponse")
        return []

    for entry in senateurs_list:
        # Gerer les deux formats de reponse :
        # {"senateur": {...}} (format avec wrapper) ou {...} (format direct)
        if isinstance(entry, dict) and "senateur" in entry:
            sen = entry.get("senateur", {})
        elif isinstance(entry, dict):
            sen = entry
        else:
            continue
        if not sen:
            continue

        nom = sen.get("nom", "").strip()
        if not nom:
            continue

        person = {
            "nom_complet": nom,
            "source": "senat",
            "source_category": "senateur",
            "occupation": "senateur",
            "slug_senat": sen.get("slug", ""),
        }

        birth = sen.get("date_naissance", "")
        if birth:
            person["date_naissance"] = birth

        lieu = sen.get("lieu_naissance", "")
        if lieu:
            person["lieu_naissance"] = lieu

        sexe = sen.get("sexe", "")
        if sexe:
            person["genre"] = "masculin" if sexe == "H" else "feminin"

        groupe = sen.get("groupe_sigle", "")
        if groupe:
            person["groupe_politique"] = groupe

        parti = sen.get("parti_ratt_financier", "")
        if parti:
            person["parti"] = parti

        profession = sen.get("profession", "")
        if profession:
            person["profession_origine"] = profession

        slug = sen.get("slug", "")
        if slug:
            person["url_nossenateurs"] = f"https://www.nossenateurs.fr/{slug}"

        persons.append(person)

    logger.info(f"[OK] Senat: {len(persons)} senateurs")
    STATS['senat_results'] += len(persons)
    return persons


# ============================================================
# HATVP enrichment
# ============================================================

# URLs du open data HATVP (inspirees de transparence-nationale)
# Le CSV index contient la liste de toutes les declarations publiees
HATVP_INDEX_URL = "https://www.hatvp.fr/livraison/opendata/liste.csv"
# URL de consultation publique des declarations
HATVP_CONSULTATION_URL = "https://www.hatvp.fr/consulter-les-declarations/"

# Cache interne du CSV HATVP (charge une seule fois par run)
_hatvp_index_cache: Optional[List[dict]] = None


def _load_hatvp_index() -> List[dict]:
    """Telecharge et parse le CSV index HATVP (liste.csv).

    Le fichier CSV contient la liste de toutes les declarations publiees.
    Colonnes attendues : civilite;prenom;nom;classement;type_mandat;qualite;
    type_document;departement;date_publication;nom_fichier;url_dossier;
    id_origine;url_photo
    """
    global _hatvp_index_cache
    if _hatvp_index_cache is not None:
        return _hatvp_index_cache

    raw = _http_get(HATVP_INDEX_URL, timeout=30, retries=1)
    if raw is None:
        logger.warning("[WARN] HATVP: impossible de telecharger l'index CSV")
        _hatvp_index_cache = []
        return _hatvp_index_cache

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            logger.warning("[WARN] HATVP: encodage CSV inconnu")
            _hatvp_index_cache = []
            return _hatvp_index_cache

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        rows = list(reader)
    except Exception as e:
        logger.warning(f"[WARN] HATVP: erreur parsing CSV: {e}")
        _hatvp_index_cache = []
        return _hatvp_index_cache

    logger.info(f"[OK] HATVP index CSV: {len(rows)} declarations")
    _hatvp_index_cache = rows
    return _hatvp_index_cache


def _normalize_hatvp_name(name: str) -> str:
    """Normalise un nom pour comparaison HATVP (minuscules, sans tirets)."""
    return name.strip().lower().replace('-', ' ')


def fetch_hatvp_for_person(name: str) -> dict:
    """Recherche une personne dans l'index CSV HATVP.

    La Haute Autorite pour la Transparence de la Vie Publique met a
    disposition un fichier CSV avec la liste de toutes les declarations
    publiees (https://www.hatvp.fr/livraison/opendata/liste.csv).
    """
    try:
        rows = _load_hatvp_index()
        if not rows:
            return {}

        # Normaliser le nom recherche
        parts = name.strip().split()
        if len(parts) < 2:
            return {}
        search_prenom = _normalize_hatvp_name(parts[0])
        search_nom = _normalize_hatvp_name(" ".join(parts[1:]))

        # Chercher dans l'index CSV
        matches = []
        for row in rows:
            csv_nom = _normalize_hatvp_name(row.get("nom", ""))
            csv_prenom = _normalize_hatvp_name(row.get("prenom", ""))
            if csv_nom == search_nom and csv_prenom == search_prenom:
                matches.append(row)

        if not matches:
            return {}

        # Trier par date de publication (plus recent en premier)
        def sort_key(r):
            d = r.get("date_publication", "")
            try:
                return datetime.strptime(d.strip(), "%Y-%m-%d")
            except (ValueError, AttributeError):
                return datetime.min
        matches.sort(key=sort_key, reverse=True)

        latest = matches[0]
        info = {
            "hatvp_declared": True,
            "hatvp_function": latest.get("qualite", "")
                or latest.get("type_mandat", ""),
            "hatvp_url": (
                f"{HATVP_CONSULTATION_URL}?nom={quote(name)}"
            ),
        }
        return info

    except Exception:
        return {}


# ============================================================
# Source 5: Repertoire National des Elus (API tabulaire data.gouv.fr)
# ============================================================

# L'API tabulaire de data.gouv.fr fournit un acces REST aux jeux
# de donnees tabulaires (CSV) heberges sur la plateforme open data.
# Base URL : https://tabular-api.data.gouv.fr/api
# Doc : https://tabular-api.data.gouv.fr/api/doc
#
# Le Repertoire National des Elus (RNE) est le jeu de donnees
# officiel du Ministere de l'Interieur contenant les informations
# sur tous les elus francais (deputes, senateurs, maires, etc).
# Dataset : https://www.data.gouv.fr/datasets/repertoire-national-des-elus-1/

TABULAR_API_BASE = "https://tabular-api.data.gouv.fr/api"
DATAGOUV_API_BASE = "https://www.data.gouv.fr/api/1"
RNE_DATASET_ID = "repertoire-national-des-elus-1"
RNE_DATASET_URL = f"https://www.data.gouv.fr/datasets/{RNE_DATASET_ID}/"

# Cache des resource IDs RNE (decouverts a l'execution)
_rne_resource_cache: Optional[dict] = None


def _discover_rne_resources() -> dict:
    """Decouvre les resource IDs du RNE via l'API datasets de data.gouv.fr.

    Interroge l'API pour obtenir la liste des ressources (fichiers CSV)
    du jeu de donnees RNE, puis associe chaque type d'elu a son resource ID.

    Retourne un dict {type_elu: resource_id} ou un dict vide en cas d'erreur.
    """
    global _rne_resource_cache
    if _rne_resource_cache is not None:
        return _rne_resource_cache

    url = f"{DATAGOUV_API_BASE}/datasets/{RNE_DATASET_ID}/"
    data = _http_get_json(url, timeout=20, retries=1)
    if data is None:
        logger.warning("[WARN] RNE: impossible de decouvrir les ressources")
        _rne_resource_cache = {}
        return _rne_resource_cache

    resources = data.get("resources", [])
    mapping = {}

    # Correspondance titre -> type d'elu
    keywords_map = {
        "deputes": ["depute", "deputes", "deputee", "deputees",
                    "député", "députés", "députée", "députées"],
        "senateurs": ["senateur", "senateurs", "senatrice", "senatrices",
                      "sénateur", "sénateurs", "sénatrice", "sénatrices"],
        "maires": ["maire", "maires"],
        "conseillers_regionaux": ["conseiller", "region", "regionaux",
                                  "régionaux"],
        "conseillers_departementaux": ["departement", "departementaux",
                                      "département", "départementaux"],
        "eurodeputes": ["eurodepute", "europeen", "eurodéputé", "européen"],
    }

    for r in resources:
        title = (r.get("title", "") or "").lower()
        rid = r.get("id", "")
        fmt = (r.get("format", "") or "").lower()
        if not rid or fmt not in ("csv", "csv.gz", ""):
            continue
        for elu_type, kws in keywords_map.items():
            if elu_type not in mapping and any(k in title for k in kws):
                mapping[elu_type] = rid
                break

    if mapping:
        logger.info(
            f"[OK] RNE: {len(mapping)} ressources decouvertes "
            f"({', '.join(mapping.keys())})"
        )
    else:
        logger.warning("[WARN] RNE: aucune ressource trouvee dans le dataset")

    _rne_resource_cache = mapping
    return _rne_resource_cache


def _fetch_tabular_page(resource_id: str, page: int = 1,
                        page_size: int = 50,
                        filters: Optional[dict] = None) -> Optional[dict]:
    """Recupere une page de donnees via l'API tabulaire de data.gouv.fr.

    L'API tabulaire fournit un acces pagine aux fichiers CSV heberges
    sur data.gouv.fr. Le format de reponse est :
    {
      "data": [...],
      "links": {"next": ..., "prev": ...},
      "meta": {"page": ..., "page_size": ..., "total": ...}
    }
    """
    url = f"{TABULAR_API_BASE}/resources/{resource_id}/data/"
    params = {"page": str(page), "page_size": str(page_size)}
    if filters:
        params.update(filters)
    query = urlencode(params)
    full_url = f"{url}?{query}"
    return _http_get_json(full_url, timeout=30, retries=1)


def _fetch_all_tabular_rows(resource_id: str,
                            max_rows: int = 500) -> List[dict]:
    """Recupere toutes les lignes d'une ressource via l'API tabulaire.

    Gere la pagination automatiquement. S'arrete apres max_rows lignes
    ou quand il n'y a plus de pages.
    """
    all_rows = []
    page = 1
    page_size = min(50, max_rows)

    while len(all_rows) < max_rows:
        data = _fetch_tabular_page(resource_id, page=page,
                                   page_size=page_size)
        if data is None:
            break

        rows = data.get("data", [])
        if not rows:
            break

        all_rows.extend(rows)

        meta = data.get("meta", {})
        total = meta.get("total", 0)
        if len(all_rows) >= total or len(all_rows) >= max_rows:
            break

        page += 1
        time.sleep(INTER_REQUEST_DELAY)

    return all_rows[:max_rows]


def _get_rne_field(row: dict, *possible_keys: str) -> str:
    """Extrait un champ d'une ligne RNE en essayant plusieurs noms de colonne.

    Les colonnes CSV du RNE peuvent varier selon la version du fichier.
    Cette fonction essaye plusieurs noms possibles et retourne la premiere
    valeur non-vide trouvee.
    """
    for key in possible_keys:
        val = row.get(key, "")
        if val and str(val).strip():
            return str(val).strip()
    return ""


def _parse_rne_person(row: dict, mandat_type: str) -> Optional[dict]:
    """Parse une ligne du RNE (API tabulaire) en dict personne.

    Les colonnes attendues du RNE incluent :
    - Nom de l'elu / nom
    - Prenom de l'elu / prenom
    - Sexe
    - Date de naissance
    - Libelle de la profession / profession
    - Code du departement / departement
    - Libelle du departement
    - Date de debut du mandat
    """
    nom = _get_rne_field(row, "Nom de l'élu", "Nom de l'elu",
                         "nom_elu", "nom", "Nom")
    prenom = _get_rne_field(row, "Prénom de l'élu", "Prenom de l'elu",
                            "prenom_elu", "prenom", "Prénom", "Prenom")
    if not nom or not prenom:
        return None

    nom_complet = f"{prenom} {nom}"

    person = {
        "nom_complet": nom_complet,
        "source": "rne_datagouv",
        "source_category": mandat_type,
        "occupation": mandat_type,
    }

    # Date de naissance
    birth = _get_rne_field(row, "Date de naissance", "date_naissance",
                           "Date de naissance de l'élu",
                           "Date de naissance de l'elu")
    if birth:
        person["date_naissance"] = birth.split("T")[0]

    # Sexe / genre
    sexe = _get_rne_field(row, "Code sexe", "Sexe", "sexe")
    if sexe:
        person["genre"] = "masculin" if sexe.upper() in ("M", "H") else "feminin"

    # Profession
    profession = _get_rne_field(row, "Libellé de la profession",
                                "Libelle de la profession",
                                "libelle_profession", "profession")
    if profession:
        person["profession_origine"] = profession

    # Departement
    dept_nom = _get_rne_field(row, "Libellé du département",
                              "Libelle du departement",
                              "libelle_departement")
    dept_code = _get_rne_field(row, "Code du département",
                               "Code du departement",
                               "code_departement")
    if dept_nom:
        person["departement"] = dept_nom
    elif dept_code:
        person["departement"] = dept_code

    # Date de debut du mandat
    debut = _get_rne_field(row, "Date de début du mandat",
                           "Date de debut du mandat",
                           "date_debut_mandat")
    if debut:
        person["date_debut_mandat"] = debut.split("T")[0]

    # Lien vers la page data.gouv.fr du dataset RNE
    person["url_datagouv"] = RNE_DATASET_URL

    return person


def fetch_rne_deputes() -> List[dict]:
    """Recupere les deputes depuis le RNE via l'API tabulaire data.gouv.fr.

    Utilise la decouverte dynamique des resource IDs via l'API datasets,
    puis interroge l'API tabulaire pour obtenir les donnees paginee.
    """
    resources = _discover_rne_resources()
    rid = resources.get("deputes", "")
    if not rid:
        logger.warning("[WARN] RNE: resource ID deputes non trouve")
        return []

    logger.info(f"RNE deputes: interrogation API tabulaire (rid={rid[:8]}...)")
    rows = _fetch_all_tabular_rows(rid, max_rows=MAX_RESULTS)

    if not rows:
        logger.warning("[WARN] RNE deputes: aucune donnee")
        return []

    persons = []
    for row in rows:
        p = _parse_rne_person(row, "depute")
        if p:
            persons.append(p)

    logger.info(f"[OK] RNE deputes (API tabulaire): {len(persons)} deputes")
    STATS['rne_results'] += len(persons)
    return persons


def fetch_rne_senateurs() -> List[dict]:
    """Recupere les senateurs depuis le RNE via l'API tabulaire data.gouv.fr.

    Utilise la decouverte dynamique des resource IDs via l'API datasets,
    puis interroge l'API tabulaire pour obtenir les donnees paginee.
    """
    resources = _discover_rne_resources()
    rid = resources.get("senateurs", "")
    if not rid:
        logger.warning("[WARN] RNE: resource ID senateurs non trouve")
        return []

    logger.info(f"RNE senateurs: interrogation API tabulaire (rid={rid[:8]}...)")
    rows = _fetch_all_tabular_rows(rid, max_rows=MAX_RESULTS)

    if not rows:
        logger.warning("[WARN] RNE senateurs: aucune donnee")
        return []

    persons = []
    for row in rows:
        p = _parse_rne_person(row, "senateur")
        if p:
            persons.append(p)

    logger.info(f"[OK] RNE senateurs (API tabulaire): {len(persons)} senateurs")
    STATS['rne_results'] += len(persons)
    return persons


# ============================================================
# Profile creation
# ============================================================

def _safe_filename(name: str) -> str:
    """Genere un nom de fichier sur pour une personne."""
    safe = re.sub(r'[^\w\s-]', '', name).strip()
    safe = re.sub(r'\s+', '-', safe)
    return safe


def _build_source_list(person_data: dict) -> List[str]:
    """Construit la liste des sources pour une fiche."""
    sources = []

    wd_url = person_data.get("wikidata_url", "")
    if wd_url:
        sources.append(wd_url)

    an_url = person_data.get("url_nosdeputes", "")
    if an_url:
        sources.append(an_url)

    senat_url = person_data.get("url_nossenateurs", "")
    if senat_url:
        sources.append(senat_url)

    hatvp_url = person_data.get("hatvp_url", "")
    if hatvp_url:
        sources.append(hatvp_url)

    datagouv_url = person_data.get("url_datagouv", "")
    if datagouv_url:
        sources.append(datagouv_url)

    return sources


def _build_bio_text(person_data: dict) -> str:
    """Construit un texte biographique a partir des donnees collectees."""
    name = person_data.get("nom_complet", "")
    occupation = person_data.get("occupation", "")
    category = person_data.get("source_category", "")
    birth = person_data.get("date_naissance", "")
    place = person_data.get("lieu_naissance", "")
    groupe = person_data.get("groupe_politique", "")
    parti = person_data.get("parti", "")
    profession = person_data.get("profession_origine", "")
    formation = person_data.get("formation", "")

    parts = []

    # Phrase d'introduction
    if category == "depute":
        parts.append(f"{name} est depute a l'Assemblee nationale.")
    elif category == "senateur":
        parts.append(f"{name} est senateur.")
    elif occupation:
        parts.append(f"{name}, {occupation}.")
    else:
        parts.append(f"{name}.")

    # Naissance
    if birth and place:
        parts.append(f"Ne(e) le {birth} a {place}.")
    elif birth:
        parts.append(f"Ne(e) le {birth}.")

    # Affiliation politique
    if groupe and parti:
        parts.append(f"Groupe politique : {groupe} ({parti}).")
    elif groupe:
        parts.append(f"Groupe politique : {groupe}.")

    # Profession d'origine
    if profession:
        parts.append(f"Profession d'origine : {profession}.")

    # Formation
    if formation:
        parts.append(f"Formation : {formation}.")

    return " ".join(parts)


def create_person_profile(person_data: dict, existing_index: Set[str]) -> Optional[str]:
    """Cree une fiche personne au format standard du depot.

    Retourne le chemin du fichier cree, ou None si pas de creation.
    """
    name = person_data.get("nom_complet", "").strip()
    if not name or len(name) < 3:
        return None

    # Verification doublon
    if _name_already_exists(name, existing_index):
        STATS['skipped_existing'] += 1
        return None

    # Dossier cible
    personnes_dir = Path("personnes")
    personnes_dir.mkdir(exist_ok=True)

    filename = _safe_filename(name)
    if not filename:
        return None

    file_path = personnes_dir / f"{filename}.md"
    if file_path.exists():
        STATS['skipped_existing'] += 1
        return None

    # Construction des sources
    sources = _build_source_list(person_data)
    source_origin = person_data.get("source", "public_api")

    # Construction du bio
    bio_text = _build_bio_text(person_data)

    # Tags
    tags = ["elite", f"source-{source_origin}"]
    category = person_data.get("source_category", "")
    if category:
        tags.append(category)

    # Keywords
    keywords = []
    occupation = person_data.get("occupation", "")
    if occupation:
        keywords.append(occupation)
    groupe = person_data.get("groupe_politique", "")
    if groupe:
        keywords.append(groupe)
    parti = person_data.get("parti", "")
    if parti:
        keywords.append(parti)

    # Formation - peut etre string ou liste
    formation_raw = person_data.get("formation", "")
    if isinstance(formation_raw, str) and formation_raw:
        education_str = formation_raw
    elif isinstance(formation_raw, list) and formation_raw:
        education_str = ", ".join(str(item) for item in formation_raw)
    else:
        education_str = None

    # Metadata YAML - utilise les noms de champs du site web
    # (birth_date, birth_place, nationality, education)
    # pour compatibilite avec index.html et les scripts existants
    metadata = {
        'type': 'Personne',
        'nom_complet': name,
        'birth_date': person_data.get('date_naissance', ''),
        'birth_place': person_data.get('lieu_naissance', ''),
        'nationality': person_data.get('nationalite', 'francaise'),
        'education': education_str,
        'occupation': occupation,
        'summary': bio_text,
        'keywords': keywords,
        'genre': person_data.get('genre', ''),
        'sources': sources,
        'statut_note': 'a_valider',
        'tags': tags,
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'aggregated_from': source_origin,
    }

    # Contenu Markdown
    content_parts = [bio_text]

    hatvp_fn = person_data.get("hatvp_function", "")
    if hatvp_fn:
        content_parts.append(f"\nFonction declaree HATVP : {hatvp_fn}")

    if sources:
        content_parts.append("\n## Sources")
        for src in sources:
            content_parts.append(f"- {src}")

    content = "\n".join(content_parts)

    if DRY_RUN:
        logger.info(f"[DRY RUN] Aurait cree : {file_path}")
        STATS['dry_run_would_create'] += 1
        return None

    # Ecriture du fichier (mode binaire pour frontmatter.dump)
    post = frontmatter.Post(content, **metadata)
    try:
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)
        STATS['files_created'] += 1
        # Ajouter au index pour eviter les doublons dans le meme run
        existing_index.add(_normalize_name(name))
        logger.info(f"[OK] Fiche creee : {file_path}")
        return str(file_path)
    except Exception as e:
        logger.error(f"[FAIL] Erreur creation {file_path}: {e}")
        STATS['creation_errors'] += 1
        return None


# ============================================================
# Self-test mode
# ============================================================

def run_self_tests() -> bool:
    """Execute les auto-tests pour valider chaque source de donnees.

    Retourne True si tous les tests passent.
    """
    print("=" * 60)
    print(" AUTO-TESTS - Validation des sources publiques")
    print("=" * 60)

    all_passed = True

    # Test 1: HTTP helper
    print("\n[TEST] HTTP helpers...")
    try:
        # Test avec donnees invalides (doit retourner None, pas crash)
        result = _http_get_json("http://invalid.test.local/fake")
        assert result is None, "Devrait retourner None pour URL invalide"
        print("  [OK] _http_get_json gere les erreurs correctement")
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        all_passed = False

    # Test 2: Name normalization
    print("\n[TEST] Normalisation des noms...")
    try:
        assert _normalize_name("Jean-Pierre Dupont") == "jean pierre dupont"
        assert _normalize_name("  Marie Curie  ") == "marie curie"
        assert _normalize_name("ALAIN-JUPPE") == "alain juppe"
        print("  [OK] Normalisation fonctionne")
    except AssertionError as e:
        print(f"  [FAIL] Normalisation: {e}")
        all_passed = False

    # Test 3: Safe filename
    print("\n[TEST] Generation de noms de fichier...")
    try:
        assert _safe_filename("Jean-Pierre Dupont") == "Jean-Pierre-Dupont"
        assert _safe_filename("Marie O'Brien") == "Marie-OBrien"
        assert _safe_filename("") == ""
        print("  [OK] Noms de fichier generes correctement")
    except AssertionError as e:
        print(f"  [FAIL] Filename: {e}")
        all_passed = False

    # Test 4: Existing index
    print("\n[TEST] Construction de l'index existant...")
    try:
        index = build_existing_index()
        assert isinstance(index, set)
        print(f"  [OK] Index construit : {len(index)} entrees")
    except Exception as e:
        print(f"  [FAIL] Index: {e}")
        all_passed = False

    # Test 5: Duplicate detection
    print("\n[TEST] Detection de doublons...")
    try:
        test_index = {"emmanuel macron", "alain juppe", "bernard arnault"}
        assert _name_already_exists("Emmanuel Macron", test_index) is True
        assert _name_already_exists("Alain Juppe", test_index) is True
        assert _name_already_exists("Personne Inexistante", test_index) is False
        print("  [OK] Detection de doublons fonctionne")
    except AssertionError as e:
        print(f"  [FAIL] Doublons: {e}")
        all_passed = False

    # Test 6: Bio text generation
    print("\n[TEST] Generation de texte biographique...")
    try:
        test_data = {
            "nom_complet": "Jean Dupont",
            "source_category": "depute",
            "date_naissance": "1970-01-15",
            "lieu_naissance": "Paris",
            "groupe_politique": "RE",
        }
        bio = _build_bio_text(test_data)
        assert "Jean Dupont" in bio
        assert "depute" in bio
        assert "1970-01-15" in bio
        assert "Paris" in bio
        assert "RE" in bio
        print(f"  [OK] Bio generee : {bio[:80]}...")
    except AssertionError as e:
        print(f"  [FAIL] Bio: {e}")
        all_passed = False

    # Test 7: Source list building
    print("\n[TEST] Construction des listes de sources...")
    try:
        test_data = {
            "wikidata_url": "https://www.wikidata.org/wiki/Q123",
            "url_nosdeputes": "https://www.nosdeputes.fr/jean-dupont",
            "hatvp_url": "https://www.hatvp.fr/consulter/?nom=Jean+Dupont",
        }
        sources = _build_source_list(test_data)
        assert len(sources) == 3
        assert any("wikidata" in s for s in sources)
        assert any("nosdeputes" in s for s in sources)
        assert any("hatvp" in s for s in sources)
        print(f"  [OK] {len(sources)} sources construites")
    except AssertionError as e:
        print(f"  [FAIL] Sources: {e}")
        all_passed = False

    # Test 8: Profile creation (dry run)
    print("\n[TEST] Creation de profil (dry run)...")
    try:
        test_index = set()
        test_person = {
            "nom_complet": "Test-Personne-Fictive-07",
            "source": "test",
            "source_category": "test",
            "date_naissance": "2000-01-01",
            "occupation": "test",
        }
        # Force dry run for test
        global DRY_RUN
        old_dry = DRY_RUN
        DRY_RUN = True
        result = create_person_profile(test_person, test_index)
        DRY_RUN = old_dry
        # Dry run returns None but does not crash
        assert result is None
        print("  [OK] Creation de profil (dry run) fonctionne")
    except Exception as e:
        print(f"  [FAIL] Creation profil: {e}")
        all_passed = False

    # Test 9: SPARQL query builder
    print("\n[TEST] Construction de requetes SPARQL...")
    try:
        query = _build_sparql_query("Q82955", 10)
        assert "Q82955" in query
        assert "Q142" in query  # France
        assert "LIMIT 10" in query
        assert "wdt:P569" in query  # birth date
        print("  [OK] Requete SPARQL construite correctement")
    except AssertionError as e:
        print(f"  [FAIL] SPARQL: {e}")
        all_passed = False

    # Test 10: RNE person parsing
    print("\n[TEST] Parsing donnees RNE...")
    try:
        rne_row = {
            "Nom de l'élu": "DUPONT",
            "Prénom de l'élu": "Jean",
            "Date de naissance": "1970-05-15",
            "Code sexe": "M",
            "Libellé de la profession": "Avocat",
            "Libellé du département": "Paris",
        }
        p = _parse_rne_person(rne_row, "depute")
        assert p is not None, "Personne RNE ne devrait pas etre None"
        assert p["nom_complet"] == "Jean DUPONT"
        assert p["date_naissance"] == "1970-05-15"
        assert p["genre"] == "masculin"
        assert p["profession_origine"] == "Avocat"
        assert p["source"] == "rne_datagouv"
        # Test avec champs vides
        empty_row = {"nom": "", "prenom": ""}
        assert _parse_rne_person(empty_row, "depute") is None
        print("  [OK] Parsing RNE fonctionne")
    except AssertionError as e:
        print(f"  [FAIL] RNE parsing: {e}")
        all_passed = False

    # Test 11: Network connectivity (informational)
    print("\n[TEST] Connectivite reseau (informatif)...")
    sources_status = []
    test_urls = [
        ("Wikidata SPARQL", WIKIDATA_SPARQL_ENDPOINT),
        ("NosDonnees.fr (AN)", AN_API_ENMANDAT_URL),
        ("NosSenateurs.fr", SENAT_API_ENMANDAT_URL),
        ("HATVP (CSV index)", HATVP_INDEX_URL),
        ("data.gouv.fr API (RNE)", f"{DATAGOUV_API_BASE}/datasets/{RNE_DATASET_ID}/"),
        ("API tabulaire data.gouv.fr", f"{TABULAR_API_BASE}/"),
    ]
    for label, url in test_urls:
        raw = _http_get(url, timeout=10)
        if raw is not None:
            sources_status.append((label, True))
            print(f"  [OK] {label} accessible")
        else:
            sources_status.append((label, False))
            print(f"  [WARN] {label} inaccessible (normal si hors-ligne)")

    accessible = sum(1 for _, ok in sources_status if ok)
    print(f"\n  Resultat connectivite : {accessible}/{len(sources_status)} sources accessibles")

    # Bilan
    print("\n" + "=" * 60)
    if all_passed:
        print(" TOUS LES TESTS PASSENT [OK]")
    else:
        print(" CERTAINS TESTS ONT ECHOUE [FAIL]")
    print("=" * 60)

    return all_passed


# ============================================================
# Main aggregation pipeline
# ============================================================

def aggregate_source(source_name: str, existing_index: Set[str]) -> List[str]:
    """Execute l'aggregation pour une source donnee.

    Retourne la liste des fichiers crees.
    """
    created_files = []

    if source_name in ("all", "wikidata"):
        logger.info("--- Source : Wikidata SPARQL ---")
        per_cat = max(5, MAX_RESULTS // len(WIKIDATA_CATEGORIES))
        persons = fetch_all_wikidata(limit_per_category=per_cat)
        for p in persons:
            path = create_person_profile(p, existing_index)
            if path:
                created_files.append(path)

    if source_name in ("all", "assemblee"):
        logger.info("--- Source : Assemblee Nationale ---")
        persons = fetch_assemblee_deputes()
        for p in persons[:MAX_RESULTS]:
            # Enrichissement HATVP pour les deputes (recherche dans le CSV local)
            hatvp = fetch_hatvp_for_person(p["nom_complet"])
            if hatvp:
                p.update(hatvp)
            path = create_person_profile(p, existing_index)
            if path:
                created_files.append(path)

    if source_name in ("all", "senat"):
        logger.info("--- Source : Senat ---")
        persons = fetch_senat_senateurs()
        for p in persons[:MAX_RESULTS]:
            hatvp = fetch_hatvp_for_person(p["nom_complet"])
            if hatvp:
                p.update(hatvp)
            path = create_person_profile(p, existing_index)
            if path:
                created_files.append(path)

    if source_name in ("all", "rne"):
        logger.info("--- Source : RNE (API tabulaire data.gouv.fr) ---")
        # Deputes RNE
        rne_deputes = fetch_rne_deputes()
        for p in rne_deputes[:MAX_RESULTS]:
            hatvp = fetch_hatvp_for_person(p["nom_complet"])
            if hatvp:
                p.update(hatvp)
            path = create_person_profile(p, existing_index)
            if path:
                created_files.append(path)
        # Senateurs RNE
        rne_senateurs = fetch_rne_senateurs()
        for p in rne_senateurs[:MAX_RESULTS]:
            hatvp = fetch_hatvp_for_person(p["nom_complet"])
            if hatvp:
                p.update(hatvp)
            path = create_person_profile(p, existing_index)
            if path:
                created_files.append(path)

    return created_files


def main(source: str = "all"):
    """Point d'entree principal du script d'aggregation."""
    global STATS

    start_time = time.time()
    STATS = defaultdict(int)

    print("=" * 60)
    print(" Aggregation de donnees publiques - Elites francaises")
    print("=" * 60)
    print(f"  Source(s) : {source}")
    print(f"  Max resultats : {MAX_RESULTS}")
    print(f"  Dry run : {'oui' if DRY_RUN else 'non'}")
    print(f"  GitHub Actions : {'oui' if IS_GITHUB_ACTION else 'non'}")
    print()

    # Construire l'index des fiches existantes
    existing_index = build_existing_index()

    # Lancer l'aggregation
    created_files = aggregate_source(source, existing_index)

    # Rapport final
    elapsed = time.time() - start_time

    print()
    print("=" * 60)
    print(" RAPPORT D'AGGREGATION")
    print("=" * 60)
    print(f"  Duree : {elapsed:.1f}s")
    print(f"  Fiches creees : {STATS['files_created']}")
    print(f"  Doublons ignores : {STATS['skipped_existing']}")
    print(f"  Resultats Wikidata : {STATS['wikidata_results']}")
    print(f"  Resultats Assemblee : {STATS['assemblee_results']}")
    print(f"  Resultats Senat : {STATS['senat_results']}")
    print(f"  Resultats RNE (API tabulaire) : {STATS['rne_results']}")
    print(f"  Erreurs HTTP : {STATS['http_errors']}")
    print(f"  Erreurs reseau : {STATS['network_errors']}")
    print(f"  Erreurs creation : {STATS['creation_errors']}")
    if DRY_RUN:
        print(f"  [DRY RUN] Aurait cree : {STATS['dry_run_would_create']}")
    print("=" * 60)

    # Commit Git si des fichiers ont ete crees
    if created_files and not DRY_RUN:
        commit_msg = (
            f"feat: aggregation sources publiques\n\n"
            f"- {STATS['files_created']} fiches creees\n"
            f"- Sources : {source}\n"
            f"- Wikidata: {STATS['wikidata_results']} resultats\n"
            f"- Assemblee: {STATS['assemblee_results']} resultats\n"
            f"- Senat: {STATS['senat_results']} resultats\n"
            f"- RNE (API tabulaire): {STATS['rne_results']} resultats\n"
            f"- Duree : {elapsed:.1f}s"
        )
        try:
            git.commit_changes(commit_msg)
            logger.info("[OK] Changements committes")
        except Exception as e:
            logger.error(f"[FAIL] Erreur commit Git : {e}")

    logger.info(f"[OK] Aggregation terminee : {STATS['files_created']} fiches creees")
    return STATS['files_created']


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--test" in args:
        success = run_self_tests()
        sys.exit(0 if success else 1)

    source_arg = "all"
    if "--source" in args:
        idx = args.index("--source")
        if idx + 1 < len(args):
            source_arg = args[idx + 1]

    valid_sources = ("all", "wikidata", "assemblee", "senat", "rne")
    if source_arg not in valid_sources:
        print(f"[FAIL] Source invalide : {source_arg}")
        print(f"  Sources valides : {', '.join(valid_sources)}")
        sys.exit(1)

    main(source_arg)

"""
OEil de Dieu - Script d'exploration de reseaux via Wikipedia

Decouvre des personnes et institutions a partir d'une requete, en utilisant
Wikipedia comme source principale et spaCy NER pour l'extraction d'entites.

Mistral est utilise pour UN SEUL appel optionnel (identification initiale).
Si Mistral est indisponible, le script fonctionne entierement via Wikipedia.

VARIABLES D'ENVIRONNEMENT:
- GITHUB_ACTIONS : Detecte automatiquement (ajuste les limites)
- MAX_ENTITIES : Override de MAX_ENTITIES_PER_RUN
- MAX_WIKI_CALLS : Override de MAX_WIKIPEDIA_CALLS
- TIME_LIMIT : Override de TIME_LIMIT_SECONDS
- MISTRAL_API_KEY : Cle API Mistral (optionnel)

EXEMPLES D'UTILISATION:
  python scripts/06_add_people_from_wikipedia.py "Le Siecle"
  MAX_ENTITIES=10 python scripts/06_add_people_from_wikipedia.py "Emmanuel Macron"
"""

import sys
import os
import wikipedia
import yaml
import frontmatter
import spacy
from pathlib import Path
from dotenv import load_dotenv
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Set
from collections import defaultdict
import time
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import URLError

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient, SDKError

# Configuration
load_dotenv()
logger = setup_logger()
git = GitHandler()
llm = MistralClient()

# Wikipedia en francais
wikipedia.set_lang("fr")

# Chargement config
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# Chargement spaCy
try:
    nlp = spacy.load("fr_core_news_lg")
except OSError:
    try:
        nlp = spacy.load("fr_core_news_md")
        logger.warning("Modele fr_core_news_lg indisponible, utilisation de fr_core_news_md")
    except OSError:
        logger.error("Aucun modele Spacy francais trouve. Lancez: python -m spacy download fr_core_news_lg")
        sys.exit(1)

# Variables globales pour tracker l'exploration
VISITED_PEOPLE: Set[str] = set()
VISITED_ORGS: Set[str] = set()
ALL_FOUND_ENTITIES = []
EXPLORATION_STATS = defaultdict(int)
RELATIONSHIPS_GRAPH = defaultdict(list)
CREATED_FILES = []
ORIGINAL_QUERY = ""
WIKIPEDIA_CALLS_COUNT = 0
START_TIME = 0

# Configuration de l'exploration
MAX_DEPTH = 3
CONFIDENCE_THRESHOLD = 0.6

# Detection de l'environnement GitHub Actions
IS_GITHUB_ACTION = os.getenv('GITHUB_ACTIONS') == 'true'

# Limites configurables
MAX_ENTITIES_PER_RUN = int(os.getenv('MAX_ENTITIES', '15' if IS_GITHUB_ACTION else '50'))
MAX_WIKIPEDIA_CALLS = int(os.getenv('MAX_WIKI_CALLS', '20' if IS_GITHUB_ACTION else '100'))
TIME_LIMIT_SECONDS = int(os.getenv('TIME_LIMIT', '300' if IS_GITHUB_ACTION else '0'))

# Timeout pour les requetes HTTP externes
HTTP_TIMEOUT = 10


# ============================================================
# Data classes
# ============================================================

class PersonEntity:
    """Classe pour representer une personne avec toutes ses metadonnees"""

    def __init__(self, name: str, depth: int, found_via: str, query: str):
        self.name = name
        self.depth = depth
        self.found_via = found_via
        self.original_query = query
        self.wikipedia_data = None
        self.validation_score = 0.0
        self.validation_reason = ""
        self.is_validated = False
        self.relationships = []
        self.organizations = []
        self.created_file_path = None
        self.factcheck_status = "pending"
        self.sources = []

    def to_dict(self) -> dict:
        """Convertit l'entite en dictionnaire"""
        return {
            'name': self.name,
            'depth': self.depth,
            'found_via': self.found_via,
            'original_query': self.original_query,
            'validation_score': self.validation_score,
            'validation_reason': self.validation_reason,
            'is_validated': self.is_validated,
            'factcheck_status': self.factcheck_status,
            'relationships_count': len(self.relationships),
            'organizations_count': len(self.organizations)
        }


class InstitutionEntity:
    """Classe pour representer une institution"""

    def __init__(self, name: str, depth: int, found_via: str):
        self.name = name
        self.depth = depth
        self.found_via = found_via
        self.wikipedia_data = None
        self.members = []
        self.created_file_path = None
        self.factcheck_status = "pending"

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'depth': self.depth,
            'found_via': self.found_via,
            'factcheck_status': self.factcheck_status,
            'members_count': len(self.members)
        }


class RelationshipDetail:
    """Classe pour representer une relation detaillee entre deux personnes"""

    def __init__(self, person_from: str, person_to: str, relationship_type: str,
                 description: str, confidence: float, source: str):
        self.person_from = person_from
        self.person_to = person_to
        self.relationship_type = relationship_type
        self.description = description
        self.confidence = confidence
        self.source = source
        self.timestamp = datetime.now()

    def to_markdown(self) -> str:
        """Convertit la relation en format Markdown pour Obsidian"""
        return f"- [[{self.person_to}]] : {self.description} ({self.relationship_type})"

    def to_dict(self) -> dict:
        return {
            'person_from': self.person_from,
            'person_to': self.person_to,
            'type': self.relationship_type,
            'description': self.description,
            'confidence': self.confidence,
            'source': self.source
        }


# ============================================================
# External data enrichment (Wikidata, HATVP)
# ============================================================

def fetch_wikidata_for_person(person_name: str) -> dict:
    """Recupere des donnees structurees complementaires depuis Wikidata.

    Source officielle et ouverte. Permet de croiser les dates, identifiants
    et fonctions avec Wikipedia pour renforcer la fiabilite factuelle.
    """
    try:
        search_url = (
            "https://www.wikidata.org/w/api.php?"
            f"action=wbsearchentities&search={quote(person_name)}&language=fr&limit=1&format=json"
        )
        req = Request(search_url, headers={"User-Agent": "FrenchConnexion/1.0"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        results = data.get("search", [])
        if not results:
            return {}

        entity_id = results[0]["id"]
        wikidata_url = f"https://www.wikidata.org/wiki/{entity_id}"

        entity_api = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
        req = Request(entity_api, headers={"User-Agent": "FrenchConnexion/1.0"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            entity_data = json.loads(resp.read().decode())

        entity = entity_data.get("entities", {}).get(entity_id, {})
        claims = entity.get("claims", {})

        info = {"wikidata_id": entity_id, "wikidata_url": wikidata_url}

        # P856 = site web officiel
        if "P856" in claims:
            try:
                info["website"] = claims["P856"][0]["mainsnak"]["datavalue"]["value"]
            except (KeyError, IndexError):
                pass

        # P569 = date de naissance
        if "P569" in claims:
            try:
                raw = claims["P569"][0]["mainsnak"]["datavalue"]["value"]["time"]
                info["birth_date_wd"] = raw.lstrip("+").split("T")[0]
            except (KeyError, IndexError):
                pass

        # P570 = date de deces
        if "P570" in claims:
            try:
                raw = claims["P570"][0]["mainsnak"]["datavalue"]["value"]["time"]
                info["death_date_wd"] = raw.lstrip("+").split("T")[0]
            except (KeyError, IndexError):
                pass

        logger.debug(f" Wikidata : {len(info)} champs pour {person_name}")
        return info

    except (URLError, json.JSONDecodeError) as e:
        logger.debug(f" Wikidata indisponible pour {person_name}: {e}")
        return {}
    except Exception as e:
        logger.debug(f" Erreur Wikidata pour {person_name}: {e}")
        return {}


def fetch_hatvp_for_person(name: str) -> dict:
    """Interroge l'API publique HATVP (Haute Autorite pour la Transparence
    de la Vie Publique) pour les declarations d'interets.

    Source officielle francaise, conformement aux obligations de transparence.
    """
    try:
        search_url = (
            f"https://www.hatvp.fr/api/v1/declarations?"
            f"nom={quote(name)}&format=json"
        )
        req = Request(search_url, headers={"User-Agent": "FrenchConnexion/1.0"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        if not data or not isinstance(data, list) or len(data) == 0:
            return {}

        declaration = data[0]
        info = {
            "hatvp_declared": True,
            "hatvp_function": declaration.get("fonction", ""),
            "hatvp_url": f"https://www.hatvp.fr/consulter-les-declarations/?nom={quote(name)}"
        }
        logger.debug(f" HATVP : declaration trouvee pour {name}")
        return info

    except (URLError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


# ============================================================
# Utility helpers
# ============================================================

def is_generic_people_term(name: str) -> bool:
    """Verifie si un nom est un terme generique (pas une personne specifique)"""
    generic_terms = [
        'dirigeants', 'membres', 'presidents', 'ministres', 'executives',
        'leaders', 'cadres', 'responsables', 'directeurs', 'personnes',
        'gens', 'individus', 'acteurs', 'participants', 'representants'
    ]

    name_lower = name.lower().strip()

    if name_lower in generic_terms:
        return True

    if not hasattr(is_generic_people_term, '_pattern'):
        escaped_terms = [re.escape(term) for term in generic_terms]
        pattern = r'\b(?:' + '|'.join(escaped_terms) + r')\b'
        is_generic_people_term._pattern = re.compile(pattern)

    return bool(is_generic_people_term._pattern.search(name_lower))


def _looks_like_person_name(name: str) -> bool:
    """Heuristic: returns True if the string looks like a person name.
    A person name typically has 2-5 capitalised words, no digits, no
    special punctuation beyond hyphens/apostrophes."""
    if not name or len(name) < 4 or len(name) > 80:
        return False
    if is_generic_people_term(name):
        return False
    # Must have at least two words
    parts = name.split()
    if len(parts) < 2 or len(parts) > 6:
        return False
    # Each word should start uppercase (allow particles like "de", "le", "du")
    particles = {'de', 'du', 'le', 'la', 'les', 'des', 'von', 'van', 'al', 'el', 'ben', 'd', 'l'}
    for part in parts:
        clean = part.strip("-'")
        if not clean:
            continue
        if clean.lower() in particles:
            continue
        if not clean[0].isupper():
            return False
    # No digits
    if re.search(r'\d', name):
        return False
    return True


def _check_limits() -> Optional[str]:
    """Check whether any run limit has been reached.
    Returns a reason string if a limit is hit, else None."""
    global WIKIPEDIA_CALLS_COUNT
    if MAX_WIKIPEDIA_CALLS > 0 and WIKIPEDIA_CALLS_COUNT >= MAX_WIKIPEDIA_CALLS:
        return "Limite appels Wikipedia atteinte"
    if TIME_LIMIT_SECONDS > 0 and (time.time() - START_TIME) >= TIME_LIMIT_SECONDS:
        return "Limite de temps atteinte"
    people_count = sum(1 for e in ALL_FOUND_ENTITIES if isinstance(e, PersonEntity))
    if people_count >= MAX_ENTITIES_PER_RUN:
        return "Limite d'entites atteinte"
    return None


def _safe_wikipedia_page(title: str):
    """Fetch a Wikipedia page, handling disambiguation and incrementing the
    global call counter. Returns (page, None) or (None, error_string)."""
    global WIKIPEDIA_CALLS_COUNT
    WIKIPEDIA_CALLS_COUNT += 1
    try:
        page = wikipedia.page(title, auto_suggest=True)
        return page, None
    except wikipedia.DisambiguationError as e:
        # Try first option
        if e.options:
            try:
                WIKIPEDIA_CALLS_COUNT += 1
                page = wikipedia.page(e.options[0], auto_suggest=False)
                EXPLORATION_STATS['factcheck_disambiguation'] += 1
                return page, None
            except Exception:
                pass
        return None, "disambiguation"
    except wikipedia.PageError:
        return None, "not_found"
    except Exception as exc:
        return None, str(exc)


# ============================================================
# Regex-based data extraction from Wikipedia text
# ============================================================

# Compiled regex patterns for French biographical data
_FRENCH_MONTHS = r'(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)'
_RE_BIRTH_DATE = re.compile(
    r'n[ée]e?\s+le\s+(\d{1,2}\s+' + _FRENCH_MONTHS + r'\s+\d{4})', re.IGNORECASE
)
_RE_BIRTH_PLACE = re.compile(
    r'n[ée]e?\s+(?:le\s+\d{1,2}\s+\w+\s+\d{4}\s+)?[àa]\s+([A-Z\u00C0-\u00FF][\w\s-]+?)(?:\s*[,\).])', re.IGNORECASE
)
_RE_DEATH_DATE = re.compile(
    r'(?:mort|d[ée]c[ée]d[ée]|decede)e?\s+le\s+(\d{1,2}\s+\w+\s+\d{4})', re.IGNORECASE
)
_RE_DEATH_DATE_ALT = re.compile(
    r'[-\u2013]\s*(?:(?:mort|decede)\s+)?(?:le\s+)?(\d{1,2}\s+\w+\s+\d{4})\s*\)', re.IGNORECASE
)
_RE_NATIONALITY = re.compile(
    r'\best\s+un(?:e)?\s+([\w]+(?:\s[\w]+)?)\s+(?:homme|femme|personnalite|politique|ecrivain|avocat|journaliste|dirigeant|ingenieur|scientifique|artiste|philosophe|economiste|universitaire|militaire|diplomate|haut fonctionnaire|fonctionnaire|chef|entrepreneur|homme d\'affaires|femme d\'affaires)',
    re.IGNORECASE
)
_RE_GENDER_F = re.compile(r'\best\s+une\s+', re.IGNORECASE)
_RE_DATES_PARENS = re.compile(
    r'\((\d{1,2}\s+\w+\s+\d{4})\s*[-\u2013]\s*(\d{1,2}\s+\w+\s+\d{4})?\)'
)
_RE_EDUCATION_KEYWORDS = [
    'diplome', 'licence', 'master', 'doctorat', 'agregation', 'ENA',
    'Sciences Po', 'Polytechnique', 'HEC', 'ENS', 'Normale Superieure',
    'universite', 'ecole', 'baccalaureat', 'formation', 'etudes'
]
_RE_CAREER_KEYWORDS = [
    'president', 'directeur', 'ministre', 'secretaire', 'depute', 'senateur',
    'PDG', 'CEO', 'fondateur', 'conseiller', 'ambassadeur', 'prefet',
    'gouverneur', 'maire', 'commissaire', 'administrateur', 'charge de mission'
]


def extract_person_data_regex(wiki_summary: str, full_content: str) -> dict:
    """Extract structured person data from Wikipedia text using regex patterns.

    Returns dict with: nom_complet_verifie, date_naissance, lieu_naissance,
    nationalite, genre, bio_courte, bio_detaillee, formation, carriere,
    mots_cles, etc.
    """
    data: Dict[str, object] = {}
    text = wiki_summary + "\n" + full_content[:5000]

    # Birth date
    m = _RE_BIRTH_DATE.search(text)
    if m:
        data['date_naissance'] = m.group(1).strip()
    else:
        m = _RE_DATES_PARENS.search(wiki_summary[:500])
        if m:
            data['date_naissance'] = m.group(1).strip()
            if m.group(2):
                data['date_deces'] = m.group(2).strip()

    # Death date
    if 'date_deces' not in data:
        m = _RE_DEATH_DATE.search(text)
        if m:
            data['date_deces'] = m.group(1).strip()
        else:
            m = _RE_DEATH_DATE_ALT.search(wiki_summary[:500])
            if m:
                data['date_deces'] = m.group(1).strip()

    # Birth place
    m = _RE_BIRTH_PLACE.search(text)
    if m:
        place = m.group(1).strip().rstrip(',).;')
        if len(place) < 60:
            data['lieu_naissance'] = place

    # Nationality
    m = _RE_NATIONALITY.search(wiki_summary)
    if m:
        data['nationalite'] = m.group(1).strip()
    elif 'francais' in wiki_summary.lower() or 'francaise' in wiki_summary.lower():
        data['nationalite'] = 'francaise'

    # Gender
    if _RE_GENDER_F.search(wiki_summary[:300]):
        data['genre'] = 'femme'
    else:
        data['genre'] = 'homme'

    # Bio
    sentences = wiki_summary.split('. ')
    data['bio_courte'] = '. '.join(sentences[:3]).strip()
    if not data['bio_courte'].endswith('.'):
        data['bio_courte'] += '.'
    data['bio_detaillee'] = wiki_summary[:1500]

    # Formation (education)
    formation = []
    for line in full_content.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        for kw in _RE_EDUCATION_KEYWORDS:
            if kw.lower() in line_stripped.lower() and len(line_stripped) < 200:
                formation.append(line_stripped.lstrip('- '))
                break
    data['formation'] = list(dict.fromkeys(formation))[:10]

    # Carriere (career roles)
    carriere = []
    for line in full_content.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        for kw in _RE_CAREER_KEYWORDS:
            if kw.lower() in line_stripped.lower() and len(line_stripped) < 200:
                carriere.append(line_stripped.lstrip('- '))
                break
    data['carriere'] = list(dict.fromkeys(carriere))[:15]

    # Distinctions
    distinctions = []
    in_distinctions = False
    for line in full_content.split('\n'):
        ls = line.strip()
        if re.match(r'={2,}\s*Distinctions?\s*={2,}', ls, re.IGNORECASE) or \
           re.match(r'#{2,}\s*Distinctions?', ls, re.IGNORECASE):
            in_distinctions = True
            continue
        if in_distinctions:
            if re.match(r'[=#]{2,}', ls):
                in_distinctions = False
                continue
            if ls.startswith('-') or ls.startswith('*'):
                distinctions.append(ls.lstrip('-* '))
    data['distinctions'] = distinctions[:10]

    # Controverses
    controverses = []
    in_controverses = False
    for line in full_content.split('\n'):
        ls = line.strip()
        if re.match(r'={2,}\s*Controverse', ls, re.IGNORECASE) or \
           re.match(r'#{2,}\s*Controverse', ls, re.IGNORECASE) or \
           re.match(r'={2,}\s*Affaire', ls, re.IGNORECASE) or \
           re.match(r'#{2,}\s*Affaire', ls, re.IGNORECASE):
            in_controverses = True
            continue
        if in_controverses:
            if re.match(r'[=#]{2,}', ls):
                in_controverses = False
                continue
            if ls and len(ls) > 10:
                controverses.append(ls.lstrip('-* '))
    data['controverses'] = controverses[:10]

    # Keywords from first paragraph
    mots_cles = set()
    doc = nlp(wiki_summary[:1000])
    for ent in doc.ents:
        if ent.label_ in ('ORG', 'LOC', 'MISC') and len(ent.text) > 2:
            mots_cles.add(ent.text)
    data['mots_cles'] = list(mots_cles)[:10]

    # Notoriete estimate based on content length
    content_len = len(full_content)
    if content_len > 20000:
        data['niveau_notoriete'] = 9
    elif content_len > 10000:
        data['niveau_notoriete'] = 7
    elif content_len > 5000:
        data['niveau_notoriete'] = 5
    elif content_len > 2000:
        data['niveau_notoriete'] = 3
    else:
        data['niveau_notoriete'] = 1

    return data


# ============================================================
# spaCy-based entity extraction
# ============================================================

def extract_relationships_spacy(person_name: str, text: str,
                                all_known_people: Set[str]) -> List[RelationshipDetail]:
    """Extract relationships using spaCy NER on Wikipedia text.
    Finds PERSON entities and creates RelationshipDetail objects."""
    relationships = []
    seen = set()
    # Process at most 10 000 chars to stay fast
    doc = nlp(text[:10000])
    for ent in doc.ents:
        if ent.label_ != 'PER':
            continue
        name = ent.text.strip()
        if not _looks_like_person_name(name):
            continue
        if name == person_name or name in seen:
            continue
        seen.add(name)
        # Determine confidence: higher if the name was already known
        confidence = 0.8 if name in all_known_people else 0.5
        # Try to extract context around mention
        start = max(0, ent.start_char - 80)
        end = min(len(text), ent.end_char + 80)
        context = text[start:end].replace('\n', ' ').strip()
        rel = RelationshipDetail(
            person_from=person_name,
            person_to=name,
            relationship_type="associe",
            description=f"Mentionne dans le meme contexte: ...{context}...",
            confidence=confidence,
            source="wikipedia_spacy_ner"
        )
        relationships.append(rel)
    return relationships


def extract_institutions_spacy(text: str) -> List[str]:
    """Extract organisation names using spaCy NER.
    Finds ORG entities in the Wikipedia text."""
    orgs = set()
    doc = nlp(text[:10000])
    for ent in doc.ents:
        if ent.label_ == 'ORG' and len(ent.text) > 2:
            orgs.add(ent.text.strip())
    return list(orgs)


# ============================================================
# Entity discovery from Wikipedia
# ============================================================

def identify_entities_from_wikipedia(query: str) -> dict:
    """Search Wikipedia for the query and extract person/institution names
    from the page. Uses spaCy NER on the page content.

    Returns dict with keys: people, institutions, main_subject, query_type,
    interpretation.
    """
    global WIKIPEDIA_CALLS_COUNT

    result = {
        'people': [],
        'institutions': [],
        'main_subject': query,
        'query_type': 'unknown',
        'interpretation': ''
    }

    # Search Wikipedia
    try:
        WIKIPEDIA_CALLS_COUNT += 1
        search_results = wikipedia.search(query, results=5)
    except Exception as e:
        logger.warning(f"[WARN] Wikipedia search failed for '{query}': {e}")
        return result

    if not search_results:
        logger.warning(f"[WARN] No Wikipedia results for '{query}'")
        return result

    # Fetch the main page
    page, err = _safe_wikipedia_page(search_results[0])
    if page is None:
        logger.warning(f"[WARN] Could not fetch Wikipedia page for '{search_results[0]}': {err}")
        return result

    result['main_subject'] = page.title
    result['interpretation'] = page.summary[:300]

    full_text = page.content[:15000]
    summary = page.summary

    # Use spaCy to extract entities
    doc = nlp(full_text[:10000])

    people = []
    institutions = []
    seen_people = set()
    seen_orgs = set()

    for ent in doc.ents:
        if ent.label_ == 'PER':
            name = ent.text.strip()
            if _looks_like_person_name(name) and name not in seen_people:
                seen_people.add(name)
                people.append(name)
        elif ent.label_ == 'ORG':
            org = ent.text.strip()
            if len(org) > 2 and org not in seen_orgs:
                seen_orgs.add(org)
                institutions.append(org)

    # Also try to extract from page links (more reliable for person names)
    try:
        links = page.links[:200]
        for link in links:
            if _looks_like_person_name(link) and link not in seen_people:
                seen_people.add(link)
                people.append(link)
    except Exception:
        pass

    # Classify query type based on page content
    person_indicators = ['est un ', 'est une ', 'ne le ', 'nee le ', 'ne en ', 'nee en ']
    org_indicators = ['est une organisation', 'est un club', 'est une association',
                      'est un think tank', 'est une societe', 'fondee en ', 'fondee le ',
                      'est une institution', 'est un groupe']

    summary_lower = summary.lower()
    if any(ind in summary_lower for ind in person_indicators):
        result['query_type'] = 'single_person'
    elif any(ind in summary_lower for ind in org_indicators):
        result['query_type'] = 'institution'
    else:
        result['query_type'] = 'people_group'

    result['people'] = people[:MAX_ENTITIES_PER_RUN]
    result['institutions'] = institutions[:20]

    logger.info(f"[OK] Wikipedia discovery: {len(people)} people, {len(institutions)} institutions from '{page.title}'")
    return result


def answer_initial_query_with_mistral(query: str) -> Optional[dict]:
    """Try to use Mistral for ONE call to identify entities from the query.
    Returns dict with people/institutions or None if Mistral unavailable."""
    if not llm.is_available():
        logger.info("[INFO] Mistral non disponible, utilisation de Wikipedia uniquement")
        return None

    prompt = f"""Analyse cette requete de recherche et identifie les personnes et institutions liees.

Requete: "{query}"

Reponds en JSON strict avec:
{{
  "query_type": "single_person" ou "institution" ou "people_group",
  "main_subject": "sujet principal",
  "interpretation": "explication courte de la requete",
  "people": ["liste", "de", "noms", "complets"],
  "institutions": ["liste", "d'institutions"]
}}

Donne uniquement des noms de personnes REELLES et CONNUES. Maximum 20 personnes."""

    try:
        EXPLORATION_STATS['mistral_calls'] += 1
        messages = [
            {"role": "system", "content": "Tu es un assistant de recherche specialise dans les reseaux de pouvoir francais. Reponds uniquement en JSON valide."},
            {"role": "user", "content": prompt}
        ]
        chat_response = llm._chat_complete_with_retry(
            model=llm.model,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        result = llm._validate_and_parse_response(chat_response, expect_json=True)
        if result and isinstance(result, dict):
            people = result.get('people', [])
            institutions = result.get('institutions', [])
            if people or institutions:
                logger.info(f"[OK] Mistral: {len(people)} personnes, {len(institutions)} institutions")
                return result
        return None
    except SDKError as e:
        logger.warning(f"[WARN] Mistral SDK error: {e}")
        return None
    except Exception as e:
        logger.warning(f"[WARN] Mistral call failed: {e}")
        return None


# ============================================================
# Wikipedia fact-checking with regex extraction
# ============================================================

def wikipedia_factcheck_person(person_name: str) -> Optional[dict]:
    """Fetch Wikipedia page for a person and extract structured data
    using regex and spaCy. Returns dict with person data or None."""
    global WIKIPEDIA_CALLS_COUNT

    page, err = _safe_wikipedia_page(person_name)
    if page is None:
        if err == "disambiguation":
            EXPLORATION_STATS['factcheck_disambiguation'] += 1
        elif err == "not_found":
            EXPLORATION_STATS['factcheck_not_found'] += 1
        else:
            EXPLORATION_STATS['factcheck_failed'] += 1
        return None

    EXPLORATION_STATS['factcheck_success'] += 1

    summary = page.summary
    full_content = page.content
    wiki_url = page.url

    # Extract structured data via regex
    data = extract_person_data_regex(summary, full_content)

    data['nom_complet_verifie'] = page.title
    data['wikipedia_title'] = page.title
    data['wikipedia_url'] = wiki_url
    data['content_length'] = len(full_content)
    data['factcheck_status'] = 'verified'
    data['verification_date'] = datetime.now().strftime('%Y-%m-%d')

    # Enrich with Wikidata
    wikidata_info = fetch_wikidata_for_person(person_name)
    if wikidata_info:
        data.update({k: v for k, v in wikidata_info.items() if k not in data or not data[k]})
        # Cross-reference dates from Wikidata
        if not data.get('date_naissance') and wikidata_info.get('birth_date_wd'):
            data['date_naissance'] = wikidata_info['birth_date_wd']
        if not data.get('date_deces') and wikidata_info.get('death_date_wd'):
            data['date_deces'] = wikidata_info['death_date_wd']

    # Enrich with HATVP
    hatvp_info = fetch_hatvp_for_person(person_name)
    if hatvp_info:
        data.update(hatvp_info)

    # Extract linked institutions via spaCy
    inst_names = extract_institutions_spacy(full_content[:8000])
    data['linked_institutions'] = inst_names

    # Compile sources
    sources = [wiki_url]
    if wikidata_info.get('wikidata_url'):
        sources.append(wikidata_info['wikidata_url'])
    if hatvp_info.get('hatvp_url'):
        sources.append(hatvp_info['hatvp_url'])
    data['all_sources'] = sources

    return data


# ============================================================
# Validation (simple heuristic, no LLM)
# ============================================================

def validate_person_simple(person: PersonEntity, parent_text: str = "") -> bool:
    """Simple validation: depth 0 always valid, depth 1+ valid if Wikipedia
    page exists AND name was found in parent's text."""
    if person.depth == 0:
        person.validation_score = 0.95
        person.validation_reason = "Sujet principal de la recherche"
        person.is_validated = True
        return True

    # For deeper entities, check that their name appears in parent context
    if parent_text and person.name in parent_text:
        person.validation_score = max(0.7, 1.0 - person.depth * 0.15)
        person.validation_reason = f"Trouve dans le texte Wikipedia de {person.found_via}"
        person.is_validated = True
        return True

    # Fallback: word-boundary match on lowercased text
    name_pattern = r'\b' + re.escape(person.name) + r'\b'
    if parent_text and re.search(name_pattern, parent_text, re.IGNORECASE):
        person.validation_score = max(0.6, 0.9 - person.depth * 0.15)
        person.validation_reason = f"Trouve (insensible a la casse) dans le texte de {person.found_via}"
        person.is_validated = True
        return True

    # If we have wikipedia data, it's likely valid
    if person.wikipedia_data:
        person.validation_score = max(0.5, 0.8 - person.depth * 0.15)
        person.validation_reason = "Page Wikipedia existante"
        person.is_validated = True
        return True

    person.validation_score = 0.3
    person.validation_reason = "Pas de page Wikipedia trouvee"
    person.is_validated = False
    return False


# ============================================================
# File creation (preserved from original)
# ============================================================

def create_person_file_comprehensive(person: PersonEntity, all_institutions: List[str]) -> bool:
    """
    Creation de fiche personne COMPLETE avec relations detaillees pour Obsidian
    """
    person_name = person.name
    person_data = person.wikipedia_data
    depth = person.depth
    found_via = person.found_via
    validation_score = person.validation_score

    if not person_data:
        logger.error(f" Pas de donnees Wikipedia pour {person_name}")
        return False

    personnes_folder = Path("personnes")
    personnes_folder.mkdir(exist_ok=True)

    safe_filename = re.sub(r'[^\w\s-]', '', person_name).strip().replace(' ', '-')
    file_path = personnes_folder / f"{safe_filename}.md"

    if file_path.exists():
        logger.info(f"  {person_name} existe deja, ignore")
        return False

    # ========== CONSTRUCTION DU CONTENU MARKDOWN ==========

    # En-tete avec contexte de decouverte
    discovery_header = ""
    if depth == 0:
        discovery_header = f">  **Sujet principal de la recherche**\n> Score de pertinence : {validation_score:.0%}\n"
    else:
        discovery_header = f">  **Decouvert via [[{found_via}]]** (niveau {depth})\n> Score de pertinence : {validation_score:.0%}\n"

    # Biographie
    bio_courte = person_data.get('bio_courte', '')
    bio_detaillee = person_data.get('bio_detaillee', '')

    bio_section = f"""## Biographie

{bio_detaillee if bio_detaillee else bio_courte}
"""

    # Section Informations personnelles
    info_section = "\n## Informations personnelles\n\n"

    if person_data.get('date_naissance'):
        info_section += f"**Date de naissance** : {person_data['date_naissance']}\n"
    if person_data.get('date_deces'):
        info_section += f"**Date de deces** : {person_data['date_deces']}\n"
    if person_data.get('lieu_naissance'):
        info_section += f"**Lieu de naissance** : {person_data['lieu_naissance']}\n"
    if person_data.get('nationalite'):
        info_section += f"**Nationalite** : {person_data['nationalite']}\n"
    if person_data.get('statut_actuel'):
        info_section += f"**Statut** : {person_data['statut_actuel']}\n"

    # Section Formation
    formation_section = ""
    formation = person_data.get('formation', [])
    if formation and len(formation) > 0:
        formation_section = "\n## Formation\n\n"
        for item in formation[:10]:
            formation_section += f"- {item}\n"

    # Section Carriere
    carriere_section = ""
    carriere = person_data.get('carriere', [])
    if carriere and len(carriere) > 0:
        carriere_section = "\n## Carriere\n\n"
        for item in carriere[:15]:
            carriere_section += f"- {item}\n"

    # Section Organisations et Institutions (avec liens Obsidian)
    org_section = ""
    institutions = person_data.get('linked_institutions', [])
    all_orgs = list(set(institutions + all_institutions))

    if all_orgs:
        org_section = "\n## Organisations et Institutions\n\n"
        for org in all_orgs[:20]:
            org_section += f"- [[{org}]]\n"

    # Section RELATIONS DETAILLEES (coeur de l'Obsidian graph)
    relations_section = ""
    relationships = person.relationships

    if relationships and len(relationships) > 0:
        relations_section = "\n## Reseau et Connexions\n\n"
        relations_section += f"*{len(relationships)} relations documentees*\n\n"

        # Grouper par type de relation
        relations_by_type = defaultdict(list)
        for rel in relationships:
            relations_by_type[rel.relationship_type].append(rel)

        for rel_type, rels in relations_by_type.items():
            relations_section += f"\n### {rel_type.capitalize()}\n\n"
            for rel in sorted(rels, key=lambda x: x.confidence, reverse=True)[:10]:
                relations_section += f"- [[{rel.person_to}]] : {rel.description} *(confiance: {rel.confidence:.0%})*\n"

    # Section Distinctions
    distinctions_section = ""
    distinctions = person_data.get('distinctions', [])
    if distinctions and len(distinctions) > 0:
        distinctions_section = "\n## Distinctions et Prix\n\n"
        for item in distinctions[:10]:
            distinctions_section += f"- {item}\n"

    # Section Controverses (transparence journalistique)
    controverses_section = ""
    controverses = person_data.get('controverses', [])
    if controverses and len(controverses) > 0:
        controverses_section = "\n## Controverses\n\n"
        for item in controverses[:10]:
            controverses_section += f"- {item}\n"

    # Mots-cles (tags Obsidian)
    mots_cles = person_data.get('mots_cles', [])
    tags_line = ""
    if mots_cles:
        tags_line = "\n**Tags** : " + " · ".join([f"#{tag.replace(' ', '-')}" for tag in mots_cles[:10]]) + "\n"

    # Footer avec metadonnees de verification et SOURCES
    all_sources = person_data.get('all_sources', [person_data.get('wikipedia_url', '')])
    sources_section = "\n## Sources\n\n"
    for src in all_sources:
        if src:
            if 'wikipedia' in src:
                sources_section += f"- [Wikipedia]({src})\n"
            elif 'wikidata' in src:
                sources_section += f"- [Wikidata]({src})\n"
            elif 'hatvp' in src:
                sources_section += f"- [HATVP - Transparence]({src})\n"
            else:
                sources_section += f"- [{src}]({src})\n"

    # Donnees HATVP (si disponibles)
    hatvp_section = ""
    if person_data.get('hatvp_declared'):
        hatvp_section = f"\n## Transparence (HATVP)\n\n"
        hatvp_section += f"**Fonction declaree** : {person_data.get('hatvp_function', 'N/A')}\n"
        hatvp_section += f"**Declarations** : [Consulter sur HATVP]({person_data.get('hatvp_url', '')})\n"

    footer = f"""
---

## Metadonnees et Verification

**Titre Wikipedia** : {person_data.get('wikipedia_title', person_name)}  
**Statut de verification** :  {person_data.get('factcheck_status', 'verified')}  
**Date de verification** : {person_data.get('verification_date', datetime.now().strftime('%Y-%m-%d'))}  
**Longueur article Wikipedia** : {person_data.get('content_length', 0)} caracteres  
**Niveau de notoriete** : {person_data.get('niveau_notoriete', 'N/A')}/10  
**Score de pertinence** : {validation_score:.0%}  
**Profondeur de recherche** : {depth}  
**Requete originale** : "{person.original_query}"  

{tags_line}

*Fiche generee le {datetime.now().strftime('%Y-%m-%d a %H:%M')} -- exploration recursive niveau {depth}*
"""

    # ========== ASSEMBLAGE FINAL ==========
    content = f"""{discovery_header}
{bio_section}
{info_section}
{formation_section}
{carriere_section}
{org_section}
{relations_section}
{distinctions_section}
{controverses_section}
{hatvp_section}
{sources_section}
{footer}
"""

    # ========== METADONNEES FRONTMATTER ==========
    metadata = {
        'type': 'personne',
        'nom_complet': person_data.get('nom_complet_verifie', person_name),
        'prenoms': person_name.split()[0] if ' ' in person_name else person_name,
        'date_naissance': person_data.get('date_naissance', ''),
        'date_deces': person_data.get('date_deces', ''),
        'lieu_naissance': person_data.get('lieu_naissance', ''),
        'nationalite': person_data.get('nationalite', ''),
        'genre': person_data.get('genre', ''),
        'statut': person_data.get('statut_actuel', ''),
        'bio': bio_courte,
        'formation': formation[:10],
        'carriere': carriere[:15],
        'affiliations': all_orgs[:20],
        'distinctions': distinctions[:10],
        'controverses': controverses[:10],
        'liens': [rel.person_to for rel in relationships[:20]],
        'relations_detaillees': [rel.to_dict() for rel in relationships[:20]],
        'presse': [],
        'sources': [s for s in person_data.get('all_sources', [person_data.get('wikipedia_url', '')]) if s],
        'wikidata_id': person_data.get('wikidata_id', ''),
        'hatvp_declared': person_data.get('hatvp_declared', False),
        'statut_note': 'verifie_wikipedia',
        'tags': ['elite', 'wikipedia', f'niveau-{depth}', 'oeil-de-dieu'] + mots_cles[:5],
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'found_via': found_via,
        'search_depth': depth,
        'verification_status': person_data.get('factcheck_status', 'verified'),
        'verification_date': person_data.get('verification_date', ''),
        'validation_score': round(validation_score, 2),
        'validation_reason': person.validation_reason,
        'original_query': person.original_query,
        'niveau_notoriete': person_data.get('niveau_notoriete', ''),
        'relationships_count': len(relationships),
        'institutions_count': len(all_orgs),
        'wikipedia_content_length': person_data.get('content_length', 0)
    }

    # ========== ECRITURE DU FICHIER ==========
    post = frontmatter.Post(content, **metadata)

    try:
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)

        person.created_file_path = str(file_path)
        CREATED_FILES.append(str(file_path))

        logger.info(f" Fiche creee : {file_path}")
        logger.info(f"   - {len(relationships)} relations detaillees")
        logger.info(f"   - {len(all_orgs)} institutions")
        logger.info(f"   - Score de validation : {validation_score:.0%}")

        EXPLORATION_STATS['files_created'] += 1

        return True

    except Exception as e:
        logger.error(f" Erreur creation fiche {person_name} : {e}")
        EXPLORATION_STATS['errors'] += 1
        return False


def create_institution_file_comprehensive(institution: InstitutionEntity) -> bool:
    """
    Creation de fiche institution COMPLETE
    """
    institution_name = institution.name
    depth = institution.depth
    found_via = institution.found_via

    institutions_folder = Path("institutions")
    institutions_folder.mkdir(exist_ok=True)

    safe_filename = re.sub(r'[^\w\s-]', '', institution_name).strip().replace(' ', '-')
    file_path = institutions_folder / f"{safe_filename}.md"

    if file_path.exists():
        logger.info(f"  Institution {institution_name} existe deja, ignore")
        return False

    # Essayer de trouver sur Wikipedia
    summary = ""
    wiki_url = ""
    verified = False
    description = ""
    extracted_data: dict = {}
    try:
        page, err = _safe_wikipedia_page(institution_name)
        if page is not None:
            summary = page.summary[:800]
            wiki_url = page.url
            verified = True
            full_content = page.content[:5000]

            # Extract metadata using regex and spaCy instead of LLM
            extracted_data = _extract_institution_data(summary, full_content)
            description = extracted_data.get('description_detaillee', summary)

            # Extract members using spaCy NER
            doc = nlp(full_content[:8000])
            membres = []
            seen = set()
            for ent in doc.ents:
                if ent.label_ == 'PER' and _looks_like_person_name(ent.text.strip()):
                    name = ent.text.strip()
                    if name not in seen:
                        seen.add(name)
                        membres.append(name)
            institution.members = membres[:20]
        else:
            raise ValueError(f"Page not found: {err}")
    except Exception:
        summary = f"Institution identifiee dans le reseau de pouvoir lie a : {found_via}"
        wiki_url = ""
        verified = False
        description = summary
        extracted_data = {}

    # Enrichissement via Wikidata
    wikidata_info = fetch_wikidata_for_person(institution_name)
    wikidata_url = wikidata_info.get('wikidata_url', '')

    # Decouverte
    discovery_text = ""
    if depth > 0:
        discovery_text = f">  **Decouvert via [[{found_via}]]** (niveau {depth})\n"
    else:
        discovery_text = f">  **Sujet principal de la recherche**\n"

    # Membres (liens Obsidian)
    membres_section = ""
    if institution.members:
        membres_section = f"\n## Membres et Dirigeants\n\n"
        for membre in institution.members[:20]:
            membres_section += f"- [[{membre}]]\n"

    # Sources
    inst_sources = []
    sources_md = "\n## Sources\n\n"
    if wiki_url:
        inst_sources.append(wiki_url)
        sources_md += f"- [Wikipedia]({wiki_url})\n"
    if wikidata_url:
        inst_sources.append(wikidata_url)
        sources_md += f"- [Wikidata]({wikidata_url})\n"
    if not inst_sources:
        sources_md += "- Aucune source verifiable\n"

    content = f"""{discovery_text}

## Description

{description}

{membres_section}

{sources_md}

---

## Metadonnees

**Type** : Institution / Organisation  
**Categorie** : {extracted_data.get('type_organisation', 'N/A')}  
**Fondation** : {extracted_data.get('date_fondation', 'N/A')}  
**Siege** : {extracted_data.get('siege_social', 'N/A')}  
**Domaine** : {extracted_data.get('domaine_activite', 'N/A')}  
**Statut de verification** : {' Verifie' if verified else ' A verifier'}  
**Date d'ajout** : {datetime.now().strftime('%Y-%m-%d')}  

*Fiche generee -- exploration recursive niveau {depth}*
"""

    metadata = {
        'type': 'institution',
        'nom': institution_name,
        'description': description,
        'type_organisation': extracted_data.get('type_organisation', ''),
        'date_fondation': extracted_data.get('date_fondation', ''),
        'siege': extracted_data.get('siege_social', ''),
        'domaine': extracted_data.get('domaine_activite', ''),
        'membres': institution.members[:20],
        'sources': inst_sources,
        'wikidata_id': wikidata_info.get('wikidata_id', ''),
        'statut_note': 'verifie_wikipedia' if verified else 'a_verifier',
        'tags': ['institution', 'elite', f'niveau-{depth}', 'oeil-de-dieu'],
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'found_via': found_via,
        'search_depth': depth,
        'verified': verified
    }

    post = frontmatter.Post(content, **metadata)

    try:
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)

        institution.created_file_path = str(file_path)
        CREATED_FILES.append(str(file_path))

        logger.info(f" Institution creee : {file_path}")
        EXPLORATION_STATS['institutions_created'] += 1

        return True

    except Exception as e:
        logger.error(f" Erreur creation institution {institution_name} : {e}")
        EXPLORATION_STATS['errors'] += 1
        return False


def _extract_institution_data(summary: str, full_content: str) -> dict:
    """Extract institution metadata from Wikipedia text using regex."""
    data: dict = {}

    text = summary + "\n" + full_content[:5000]
    data['description_detaillee'] = summary

    # Date fondation
    m = re.search(r'(?:fond[ée]e?|cr[eé]{1,2}e?)\s+(?:en|le)\s+(\d{4}|\d{1,2}\s+\w+\s+\d{4})', text, re.IGNORECASE)
    if m:
        data['date_fondation'] = m.group(1)

    # Siege
    m = re.search(r'(?:si[eè]ge|bas[ée]e?)\s+(?:social\s+)?(?:[àa]|est\s+[àa])\s+([A-Z\u00C0-\u00FF][\w\s,-]+?)(?:\.|,|\n)', text)
    if m:
        data['siege_social'] = m.group(1).strip()

    # Type organisation
    org_types = {
        'think tank': 'think tank', 'club': 'club', 'association': 'association',
        'entreprise': 'entreprise', 'societe': 'societe', 'fondation': 'fondation',
        'syndicat': 'syndicat', 'parti': 'parti politique', 'banque': 'banque',
        'ministere': 'institution publique', 'institut': 'institut'
    }
    summary_lower = summary.lower()
    for keyword, label in org_types.items():
        if keyword in summary_lower:
            data['type_organisation'] = label
            break

    # Domaine
    domain_keywords = {
        'politique': 'politique', 'economie': 'economie', 'finance': 'finance',
        'media': 'medias', 'defense': 'defense', 'industrie': 'industrie',
        'education': 'education', 'sante': 'sante', 'technologie': 'technologie',
        'recherche': 'recherche', 'culture': 'culture'
    }
    for keyword, label in domain_keywords.items():
        if keyword in summary_lower:
            data['domaine_activite'] = label
            break

    return data


# ============================================================
# Report generation
# ============================================================

def generate_exploration_report(query: str, validated: List[PersonEntity],
                                rejected: List[PersonEntity]) -> str:
    """Genere un rapport detaille de l'exploration"""
    report = f"""
{'='*70}
 RAPPORT D'EXPLORATION - OEil de Dieu
{'='*70}

REQUETE ORIGINALE : "{query}"
DATE : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*70}
STATISTIQUES GLOBALES
{'='*70}

Profondeur d'exploration : {MAX_DEPTH} niveaux
Seuil de confiance : {CONFIDENCE_THRESHOLD:.0%}

Appels Mistral : {EXPLORATION_STATS['mistral_calls']}
Entites identifiees : {EXPLORATION_STATS['entities_identified']}
Institutions identifiees : {EXPLORATION_STATS['institutions_identified']}
Relations extraites : {EXPLORATION_STATS['relationships_extracted']}

Factchecks Wikipedia :
  - Reussis : {EXPLORATION_STATS['factcheck_success']}
  - Non trouves : {EXPLORATION_STATS['factcheck_not_found']}
  - Ambiguites resolues : {EXPLORATION_STATS['factcheck_disambiguation']}
  - Echecs : {EXPLORATION_STATS['factcheck_failed']}

Validations :
  - Effectuees : {EXPLORATION_STATS['validations_performed']}
  - Acceptees : {EXPLORATION_STATS['validations_passed']}
  - Rejetees : {EXPLORATION_STATS['validations_rejected']}

Fichiers crees :
  - Personnes : {EXPLORATION_STATS['files_created']}
  - Institutions : {EXPLORATION_STATS['institutions_created']}
  - Total : {EXPLORATION_STATS['files_created'] + EXPLORATION_STATS['institutions_created']}

Erreurs : {EXPLORATION_STATS['errors']}

LIMITES :
  - Environnement : {'GitHub Actions' if IS_GITHUB_ACTION else 'Local'}
  - Limite d'entites : {MAX_ENTITIES_PER_RUN}
  - Limite appels Wikipedia : {MAX_WIKIPEDIA_CALLS if MAX_WIKIPEDIA_CALLS > 0 else 'Aucune'}
  - Limite de temps : {TIME_LIMIT_SECONDS}s ({TIME_LIMIT_SECONDS//60}min) si > 0
  - Appels Wikipedia effectues : {WIKIPEDIA_CALLS_COUNT}

{'='*70}
PERSONNES VALIDEES ({len(validated)})
{'='*70}

"""

    validated_sorted = sorted(validated, key=lambda x: x.validation_score, reverse=True)

    for i, person in enumerate(validated_sorted, 1):
        reason_text = person.validation_reason[:100] if person.validation_reason else "N/A"
        report += f"""
{i}. {person.name}
   Profondeur : {person.depth}
   Score : {person.validation_score:.0%}
   Trouve via : {person.found_via}
   Relations : {len(person.relationships)}
   Raison : {reason_text}
"""

    report += f"""
{'='*70}
PERSONNES REJETEES ({len(rejected)})
{'='*70}

"""

    rejected_sorted = sorted(rejected, key=lambda x: x.validation_score, reverse=True)

    for i, person in enumerate(rejected_sorted, 1):
        reason_text = person.validation_reason[:100] if person.validation_reason else "N/A"
        report += f"""
{i}. {person.name}
   Profondeur : {person.depth}
   Score : {person.validation_score:.0%}
   Raison du rejet : {reason_text}
"""

    # Avoid division by zero
    total = len(validated) + len(rejected)
    validation_rate = (len(validated) / total * 100) if total > 0 else 0
    avg_validated = (sum(p.validation_score for p in validated) / len(validated)) if validated else 0
    avg_rejected = (sum(p.validation_score for p in rejected) / len(rejected)) if rejected else 0

    report += f"""
{'='*70}
ANALYSE DE QUALITE
{'='*70}

Taux de validation : {validation_rate:.1f}%
Score moyen des valides : {avg_validated:.0%}
Score moyen des rejetes : {avg_rejected:.0%}

Distribution par profondeur :
"""

    for depth in range(MAX_DEPTH):
        count = len([p for p in validated if p.depth == depth])
        report += f"  Niveau {depth} : {count} personnes\n"

    report += f"""
{'='*70}
FIN DU RAPPORT
{'='*70}
"""

    return report


# ============================================================
# Exploration engine
# ============================================================

def explore_person(person_name: str, depth: int, found_via: str,
                   parent_text: str, all_known_people: Set[str]) -> Optional[PersonEntity]:
    """Process a single person: fetch Wikipedia, extract data, validate.
    Returns PersonEntity or None."""
    global WIKIPEDIA_CALLS_COUNT

    if person_name in VISITED_PEOPLE:
        return None
    VISITED_PEOPLE.add(person_name)

    limit_reason = _check_limits()
    if limit_reason:
        logger.info(f"[WARN] {limit_reason}, skip {person_name}")
        return None

    person = PersonEntity(person_name, depth, found_via, ORIGINAL_QUERY)

    # Fetch and extract from Wikipedia
    wiki_data = wikipedia_factcheck_person(person_name)
    if wiki_data is None:
        # Validate without Wikipedia data
        validate_person_simple(person, parent_text)
        EXPLORATION_STATS['validations_performed'] += 1
        if not person.is_validated:
            EXPLORATION_STATS['validations_rejected'] += 1
            return person
        return person

    person.wikipedia_data = wiki_data
    person.factcheck_status = "verified"

    # Extract relationships via spaCy
    full_text = ""
    try:
        page, _ = _safe_wikipedia_page(person_name)
        if page:
            full_text = page.content[:10000]
    except Exception:
        pass

    relationships = extract_relationships_spacy(person_name, full_text, all_known_people)
    person.relationships = relationships
    EXPLORATION_STATS['relationships_extracted'] += len(relationships)

    # Extract institutions
    inst_names = wiki_data.get('linked_institutions', [])
    person.organizations = inst_names

    # Validate
    validate_person_simple(person, parent_text)
    EXPLORATION_STATS['validations_performed'] += 1
    if person.is_validated:
        EXPLORATION_STATS['validations_passed'] += 1
    else:
        EXPLORATION_STATS['validations_rejected'] += 1

    return person


def explore_network(query: str, initial_people: List[str],
                    initial_institutions: List[str], query_type: str):
    """Main exploration loop. Processes entities breadth-first up to MAX_DEPTH."""
    global ALL_FOUND_ENTITIES

    all_known_people: Set[str] = set(initial_people)

    # Queue: (name, depth, found_via, parent_text)
    queue: List[tuple] = []

    # Seed depth-0 entities
    if query_type == 'single_person' and initial_people:
        # The first person is the main subject
        queue.append((initial_people[0], 0, query, ""))
        for name in initial_people[1:]:
            queue.append((name, 1, initial_people[0], ""))
    else:
        for name in initial_people:
            queue.append((name, 0, query, ""))

    # Process institutions at depth 0
    for inst_name in initial_institutions:
        if inst_name in VISITED_ORGS:
            continue
        VISITED_ORGS.add(inst_name)
        institution = InstitutionEntity(inst_name, 0, query)
        ALL_FOUND_ENTITIES.append(institution)
        EXPLORATION_STATS['institutions_identified'] += 1

    processed_at_depth: Dict[int, int] = defaultdict(int)

    while queue:
        name, depth, found_via, parent_text = queue.pop(0)

        if depth >= MAX_DEPTH:
            continue

        limit_reason = _check_limits()
        if limit_reason:
            logger.info(f"[WARN] {limit_reason}, arret exploration")
            break

        people_count = sum(1 for e in ALL_FOUND_ENTITIES if isinstance(e, PersonEntity))
        total = len(queue) + people_count
        elapsed = time.time() - START_TIME
        logger.info(f"[INFO] Processing {name} (depth={depth}, {people_count}/{MAX_ENTITIES_PER_RUN} entities, {elapsed:.0f}s)")

        person = explore_person(name, depth, found_via, parent_text, all_known_people)
        if person is None:
            continue

        ALL_FOUND_ENTITIES.append(person)
        EXPLORATION_STATS['entities_identified'] += 1
        processed_at_depth[depth] += 1

        # If validated and has wikipedia data, queue related people for next depth
        if person.is_validated and person.wikipedia_data and depth + 1 < MAX_DEPTH:
            # Get the full text for child discovery
            child_parent_text = person.wikipedia_data.get('bio_detaillee', '')

            for rel in person.relationships:
                if rel.confidence >= CONFIDENCE_THRESHOLD and rel.person_to not in VISITED_PEOPLE:
                    all_known_people.add(rel.person_to)
                    queue.append((rel.person_to, depth + 1, person.name, child_parent_text))

            # Also create institution entities for discovered orgs
            for org_name in person.organizations[:5]:
                if org_name not in VISITED_ORGS and len(org_name) > 3:
                    VISITED_ORGS.add(org_name)
                    inst = InstitutionEntity(org_name, depth + 1, person.name)
                    ALL_FOUND_ENTITIES.append(inst)
                    EXPLORATION_STATS['institutions_identified'] += 1


# ============================================================
# Main
# ============================================================

def main(query: str = None):
    """OEil de Dieu - Exploration de reseaux via Wikipedia + spaCy"""
    global VISITED_PEOPLE, VISITED_ORGS, ALL_FOUND_ENTITIES, ORIGINAL_QUERY
    global EXPLORATION_STATS, RELATIONSHIPS_GRAPH, CREATED_FILES
    global WIKIPEDIA_CALLS_COUNT, START_TIME

    # Reinitialisation
    VISITED_PEOPLE = set()
    VISITED_ORGS = set()
    ALL_FOUND_ENTITIES = []
    EXPLORATION_STATS = defaultdict(int)
    RELATIONSHIPS_GRAPH = defaultdict(list)
    CREATED_FILES = []
    WIKIPEDIA_CALLS_COUNT = 0
    START_TIME = time.time()

    print("\n" + "="*70)
    print(" OEil de Dieu - Construction de reseau de pouvoir")
    print("="*70)
    print(f"\n  Parametres :")
    print(f"  - Environnement : {'GitHub Actions' if IS_GITHUB_ACTION else 'Local'}")
    print(f"  - Profondeur maximale : {MAX_DEPTH}")
    print(f"  - Limite d'entites : {MAX_ENTITIES_PER_RUN}")
    print(f"  - Limite Wikipedia : {MAX_WIKIPEDIA_CALLS}")
    if TIME_LIMIT_SECONDS > 0:
        print(f"  - Limite de temps : {TIME_LIMIT_SECONDS}s ({TIME_LIMIT_SECONDS//60}min)")
    print(f"  - Seuil de confiance : {CONFIDENCE_THRESHOLD:.0%}")
    print(f"  - Mistral disponible : {'oui' if llm.is_available() else 'non'}")
    print("="*70)

    if not query:
        print("\nExemples de requetes :")
        print("  - Le Siecle")
        print("  - Jeffrey Epstein")
        print("  - Emmanuel Macron")
        print("  - Groupe Bilderberg")
        print("  - Bernard Arnault")
        print("="*70)

        query = input("\n Entite a explorer : ").strip()

    if not query:
        logger.error("[FAIL] Requete vide, abandon")
        return

    ORIGINAL_QUERY = query

    logger.info(f"[OK] Lancement de l'exploration : '{query}'")

    # ========== PHASE 0 : IDENTIFICATION DES ENTITES ==========
    print(f"\n Phase 0 : Identification des entites...\n")

    initial_answer = None
    initial_people = []
    initial_institutions = []
    query_type = 'unknown'

    # Try Mistral first (1 optional call)
    initial_answer = answer_initial_query_with_mistral(query)

    if initial_answer:
        initial_people = initial_answer.get('people', [])
        initial_institutions = initial_answer.get('institutions', [])
        query_type = initial_answer.get('query_type', 'unknown')
        print(f"  [OK] Mistral: {len(initial_people)} personnes, {len(initial_institutions)} institutions")
    else:
        print(f"  [INFO] Mistral non utilise, decouverte via Wikipedia")

    # Always supplement with Wikipedia discovery
    wiki_discovery = identify_entities_from_wikipedia(query)
    wiki_people = wiki_discovery.get('people', [])
    wiki_institutions = wiki_discovery.get('institutions', [])

    if query_type == 'unknown':
        query_type = wiki_discovery.get('query_type', 'unknown')

    # Merge results (Mistral first, then Wikipedia additions)
    seen_people = set(p.lower() for p in initial_people)
    for p in wiki_people:
        if p.lower() not in seen_people:
            seen_people.add(p.lower())
            initial_people.append(p)

    seen_institutions = set(i.lower() for i in initial_institutions)
    for i in wiki_institutions:
        if i.lower() not in seen_institutions:
            seen_institutions.add(i.lower())
            initial_institutions.append(i)

    print(f"\n  Analyse de la requete :")
    print(f"   - Type : {query_type}")
    print(f"   - {len(initial_people)} personnes identifiees")
    print(f"   - {len(initial_institutions)} institutions identifiees")

    if not initial_people and not initial_institutions:
        logger.warning("[WARN] Aucune entite trouvee pour cette requete")
        return

    # ========== PHASE 1 : EXPLORATION ==========
    print(f"\n Phase 1 : Exploration du reseau ({MAX_DEPTH} niveaux)...\n")
    explore_network(query, initial_people, initial_institutions, query_type)

    if not ALL_FOUND_ENTITIES:
        logger.warning("[WARN] Aucune entite trouvee")
        return

    # Separer personnes et institutions
    people_entities = [e for e in ALL_FOUND_ENTITIES if isinstance(e, PersonEntity)]
    institution_entities = [e for e in ALL_FOUND_ENTITIES if isinstance(e, InstitutionEntity)]

    # Split validated vs rejected
    validated_people = [p for p in people_entities if p.is_validated]
    rejected_people = [p for p in people_entities if not p.is_validated]

    print(f"\n Exploration terminee :")
    print(f"   - {len(people_entities)} personnes decouvertes")
    print(f"   - {len(institution_entities)} institutions decouvertes")
    print(f"   - {EXPLORATION_STATS['relationships_extracted']} relations extraites")
    print(f"   - {len(validated_people)} validees, {len(rejected_people)} rejetees")

    if not validated_people and not institution_entities:
        logger.warning("[WARN] Aucune entite validee a creer")
        return

    # ========== PHASE 2 : VERIFICATION DES FICHIERS EXISTANTS ==========
    print(f"\n Phase 2 : Verification des fichiers existants...\n")

    personnes_folder = Path("personnes")
    institutions_folder = Path("institutions")

    existing_people_files = set()
    existing_institution_files = set()

    if personnes_folder.exists():
        existing_people_files = {f.stem for f in personnes_folder.glob("*.md")}
        logger.info(f" {len(existing_people_files)} fichiers personnes existants trouves")

    if institutions_folder.exists():
        existing_institution_files = {f.stem for f in institutions_folder.glob("*.md")}
        logger.info(f" {len(existing_institution_files)} fichiers institutions existants trouves")

    people_to_create = []
    people_already_exist = []

    for person in validated_people:
        safe_filename = re.sub(r'[^\w\s-]', '', person.name).strip().replace(' ', '-')
        if safe_filename in existing_people_files:
            people_already_exist.append(person.name)
            logger.info(f"  {person.name} existe deja, skip")
        else:
            people_to_create.append(person)

    institutions_to_create = []
    institutions_already_exist = []

    for inst in institution_entities:
        safe_filename = re.sub(r'[^\w\s-]', '', inst.name).strip().replace(' ', '-')
        if safe_filename in existing_institution_files:
            institutions_already_exist.append(inst.name)
            logger.info(f"  Institution {inst.name} existe deja, skip")
        else:
            institutions_to_create.append(inst)

    print(f"\n  Bilan des fichiers a creer :")
    print(f"   - Personnes : {len(people_to_create)} nouvelles ({len(people_already_exist)} existent deja)")
    print(f"   - Institutions : {len(institutions_to_create)} nouvelles ({len(institutions_already_exist)} existent deja)")

    if not people_to_create and not institutions_to_create:
        print(f"\n  Toutes les entites existent deja, aucune creation necessaire")
        logger.info("[OK] Toutes les entites existent deja")
        return

    # ========== PHASE 3 : CREATION DES FICHIERS ==========
    print(f"\n Phase 3 : Creation des fiches...\n")

    all_institutions_names = [inst.name for inst in institution_entities]

    people_created = 0
    people_errors = 0

    for person in people_to_create:
        try:
            if create_person_file_comprehensive(person, all_institutions_names):
                people_created += 1
                print(f"    {person.name} (score: {person.validation_score:.0%}, niveau: {person.depth})")
            else:
                people_errors += 1
        except Exception as e:
            logger.error(f"[FAIL] Erreur creation {person.name} : {e}")
            people_errors += 1
            EXPLORATION_STATS['errors'] += 1

    institutions_created = 0
    institutions_errors = 0

    for inst in institutions_to_create:
        try:
            if create_institution_file_comprehensive(inst):
                institutions_created += 1
                print(f"    {inst.name} (niveau: {inst.depth})")
            else:
                institutions_errors += 1
        except Exception as e:
            logger.error(f"[FAIL] Erreur creation institution {inst.name} : {e}")
            institutions_errors += 1
            EXPLORATION_STATS['errors'] += 1

    # ========== PHASE 4 : RAPPORT ==========
    print(f"\n Phase 4 : Generation du rapport...\n")

    report = generate_exploration_report(query, validated_people, rejected_people)

    reports_folder = Path("rapports")
    reports_folder.mkdir(exist_ok=True)

    report_filename = f"rapport_exploration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = reports_folder / report_filename

    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"[OK] Rapport sauvegarde : {report_path}")
        print(f"    Rapport sauvegarde : {report_path}")
    except Exception as e:
        logger.error(f"[FAIL] Erreur sauvegarde rapport : {e}")

    # ========== PHASE 5 : RESUME FINAL ==========
    elapsed_time = time.time() - START_TIME
    total_created = people_created + institutions_created

    print("\n" + "="*70)
    print(" RESULTAT FINAL")
    print("="*70)

    print(f"\n  Duree : {elapsed_time:.1f}s ({elapsed_time/60:.1f}min)")
    print(f"  Personnes creees : {people_created}")
    print(f"  Institutions creees : {institutions_created}")
    print(f"  Relations extraites : {EXPLORATION_STATS['relationships_extracted']}")
    print(f"  Appels Wikipedia : {WIKIPEDIA_CALLS_COUNT}")
    print(f"  Appels Mistral : {EXPLORATION_STATS['mistral_calls']}")
    print(f"  Erreurs : {people_errors + institutions_errors}")

    if validated_people:
        avg_score = sum(p.validation_score for p in validated_people) / len(validated_people)
        print(f"  Score moyen : {avg_score:.0%}")

    # ========== PHASE 6 : COMMIT GIT ==========
    if total_created > 0:
        print(f"\n Phase 5 : Commit Git...")

        total_val = len(validated_people) + len(rejected_people)
        val_rate = (len(validated_people) / total_val * 100) if total_val > 0 else 0
        avg_score = (sum(p.validation_score for p in validated_people) / len(validated_people)) if validated_people else 0
        avg_rels = (sum(len(p.relationships) for p in validated_people) / len(validated_people)) if validated_people else 0

        commit_msg = f"""feat: OEil de Dieu - Exploration '{query}'

Statistiques :
- {people_created} personnes creees
- {institutions_created} institutions creees
- {len(validated_people)} personnes validees (taux: {val_rate:.1f}%)
- {EXPLORATION_STATS['relationships_extracted']} relations extraites
- Exploration sur {MAX_DEPTH} niveaux
- Duree : {elapsed_time:.1f}s
- Appels Wikipedia : {WIKIPEDIA_CALLS_COUNT}
- Appels Mistral : {EXPLORATION_STATS['mistral_calls']}

Requete originale : "{ORIGINAL_QUERY}"
"""

        try:
            git.commit_changes(commit_msg)
            print("[OK] Changements committes avec succes")
            logger.info("[OK] Changements committes")
        except Exception as e:
            logger.error(f"[FAIL] Erreur commit Git : {e}")
            print(f"[FAIL] Erreur commit Git : {e}")
    else:
        print("\n  Aucun fichier cree, pas de commit Git")

    print("\n" + "="*70)
    print(" Exploration terminee")
    print("="*70)

    logger.info(f"[OK] Exploration terminee : {total_created} entites creees")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        query_arg = ' '.join(sys.argv[1:])
        main(query_arg)
    else:
        main()
import sys
import os
import json
import re
import time
import wikipedia
import frontmatter
from pathlib import Path
from dotenv import load_dotenv
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import URLError

# Ajout du path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.llm_client import MistralClient

# Configuration
load_dotenv()
logger = setup_logger()
llm = MistralClient()

# Wikipedia en francais
wikipedia.set_lang("fr")

# Timeout pour les requetes HTTP externes
HTTP_TIMEOUT = 10

# Delay between processing each entity to avoid Wikipedia rate limits
INTER_ENTITY_DELAY = float(os.getenv("INTER_ENTITY_DELAY", "1.5"))


# ========== REGEX-BASED EXTRACTION (no LLM needed) ==========

# Date patterns: "ne le 15 avril 1969", "1er janvier 2000", "YYYY-MM-DD"
_DATE_PATTERNS = [
    r'(\d{4}-\d{2}-\d{2})',
    r'(\d{1,2}(?:er)?\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+\d{4})',
]

_BIRTH_PATTERNS = [
    r'n[eé]e?\s+le\s+(.{10,40}?)(?:\s+[àa]\s+(.+?))?(?:\)|,|\.|$)',
    r'n[eé]e?\s+(?:le\s+)?(\d{1,2}(?:er)?\s+\w+\s+\d{4})(?:\s+[àa]\s+(.+?))?(?:\)|,|\.|$)',
    r'\((\d{1,2}(?:er)?\s+\w+\s+\d{4})\s*[-–]\s*',
]

_NATIONALITY_PATTERNS = [
    r'est\s+un(?:e)?\s+(?:homme|femme)\s+(?:politique\s+|d[\'  ](?:affaires|[eéÉ]tat)\s+)?(\w+)',
    r'de\s+nationalit[eé]\s+(\w+)',
]

_FOUNDED_PATTERNS = [
    r'fond[eé]e?\s+(?:le\s+|en\s+)(.{5,40}?)(?:\)|,|\.|$)',
    r'cr[eé][eé]e?\s+(?:le\s+|en\s+)(.{5,40}?)(?:\)|,|\.|$)',
    r'[eé]tabli(?:e|s)?\s+en\s+(\d{4})',
]

_HEADQUARTERS_PATTERNS = [
    r'si[eè]ge\s+(?:social\s+)?(?:est\s+)?(?:situ[eé]\s+)?(?:[àa]\s+|en\s+)(.{3,50}?)(?:\.|,|$)',
    r'bas[eé]e?\s+[àa]\s+(.{3,50}?)(?:\.|,|$)',
]


def _first_match(patterns, text):
    """Return the first regex match from a list of patterns, or None."""
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m
    return None


def extract_person_data_regex(summary: str) -> dict:
    """Extract structured person data from Wikipedia summary using regex."""
    data = {}

    # Birth date
    m = _first_match(_BIRTH_PATTERNS, summary)
    if m:
        data['birth_date'] = m.group(1).strip()
        if m.lastindex >= 2 and m.group(2):
            data['birth_place'] = m.group(2).strip().rstrip(')')

    # Nationality
    m = _first_match(_NATIONALITY_PATTERNS, summary)
    if m:
        data['nationality'] = m.group(1).strip()

    # Occupation: first sentence often describes the role
    first_sentence = summary.split('.')[0] if '.' in summary else summary
    occupation_match = re.search(
        r'est\s+un(?:e)?\s+(.{5,80}?)(?:\.|,|$)', first_sentence, re.IGNORECASE
    )
    if occupation_match:
        data['occupation'] = occupation_match.group(1).strip()

    return data


def extract_org_data_regex(summary: str) -> dict:
    """Extract structured organization data from Wikipedia summary using regex."""
    data = {}

    # Founded date
    m = _first_match(_FOUNDED_PATTERNS, summary)
    if m:
        data['founded'] = m.group(1).strip()

    # Headquarters
    m = _first_match(_HEADQUARTERS_PATTERNS, summary)
    if m:
        data['headquarters'] = m.group(1).strip()

    # Industry/sector from first sentence
    first_sentence = summary.split('.')[0] if '.' in summary else summary
    industry_match = re.search(
        r'est\s+un(?:e)?\s+(.{5,80}?)(?:\.|,|$)', first_sentence, re.IGNORECASE
    )
    if industry_match:
        data['industry'] = industry_match.group(1).strip()

    return data


def extract_data_from_summary(wiki_summary: str, entity_type: str) -> dict:
    """Extract structured data from Wikipedia summary.

    Tries Mistral first for better quality, falls back to regex patterns.
    """
    extracted_data = {}

    # Try Mistral if available
    if llm.is_available():
        schema = get_schema_for_type(entity_type)
        if schema != "{}":
            try:
                extracted_data = llm.extract_yaml_data(wiki_summary, schema)
            except Exception as e:
                logger.warning(f"[WARN] Mistral extraction echouee, fallback regex : {e}")
                extracted_data = {}

    # Fallback (or complement) with regex extraction
    if not extracted_data:
        if entity_type == "Personne":
            extracted_data = extract_person_data_regex(wiki_summary)
        elif entity_type in ("Institution", "Entreprise", "Ecole", "Media", "Fondation"):
            extracted_data = extract_org_data_regex(wiki_summary)

    return extracted_data


def get_schema_for_type(entity_type):
    """Definit les champs precis a extraire selon le type de fiche."""
    if entity_type == "Personne":
        return """
        {
          "birth_date": "Date de naissance (format YYYY-MM-DD ou texte simple)",
          "birth_place": "Lieu de naissance (Ville, Pays)",
          "nationality": "Nationalite",
          "occupation": "Profession ou role principal",
          "education": "Diplome ou formation (alma_mater)",
          "website": "Site web officiel (URL)"
        }
        """
    elif entity_type in ["Institution", "Entreprise", "Ecole", "Media", "Fondation"]:
        return """
        {
          "founded": "Date de creation ou fondation",
          "headquarters": "Ville ou pays du siege social",
          "leader": "Nom du dirigeant actuel (PDG, President, Directeur)",
          "industry": "Secteur d'activite",
          "website": "Site web officiel (URL)"
        }
        """
    else:
        return "{}"


def fetch_wikidata_info(title: str) -> dict:
    """Recupere des donnees structurees depuis Wikidata via l'API publique.
    
    Wikidata fournit des metadonnees factuelles verifiables : dates, identifiants
    officiels, liens vers d'autres bases de donnees institutionnelles.
    """
    try:
        search_url = (
            "https://www.wikidata.org/w/api.php?"
            f"action=wbsearchentities&search={quote(title)}&language=fr&limit=1&format=json"
        )
        req = Request(search_url, headers={"User-Agent": "FrenchConnexion/1.0"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        results = data.get("search", [])
        if not results:
            return {}

        entity_id = results[0]["id"]

        entity_url = (
            f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
        )
        req = Request(entity_url, headers={"User-Agent": "FrenchConnexion/1.0"})
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            entity_data = json.loads(resp.read().decode())

        entity = entity_data.get("entities", {}).get(entity_id, {})
        claims = entity.get("claims", {})

        info = {"wikidata_id": entity_id}

        # P856 = site web officiel
        if "P856" in claims:
            try:
                info["website_wikidata"] = claims["P856"][0]["mainsnak"]["datavalue"]["value"]
            except (KeyError, IndexError):
                pass

        # P569 = date de naissance
        if "P569" in claims:
            try:
                raw = claims["P569"][0]["mainsnak"]["datavalue"]["value"]["time"]
                info["birth_date_wikidata"] = raw.lstrip("+").split("T")[0]
            except (KeyError, IndexError):
                pass

        # P570 = date de deces
        if "P570" in claims:
            try:
                raw = claims["P570"][0]["mainsnak"]["datavalue"]["value"]["time"]
                info["death_date_wikidata"] = raw.lstrip("+").split("T")[0]
            except (KeyError, IndexError):
                pass

        # P571 = date de fondation (pour les organisations)
        if "P571" in claims:
            try:
                raw = claims["P571"][0]["mainsnak"]["datavalue"]["value"]["time"]
                info["founded_wikidata"] = raw.lstrip("+").split("T")[0]
            except (KeyError, IndexError):
                pass

        # P39 = fonctions occupees (liste)
        if "P39" in claims:
            positions = []
            for claim in claims["P39"][:5]:
                try:
                    pos_id = claim["mainsnak"]["datavalue"]["value"]["id"]
                    positions.append(pos_id)
                except (KeyError, IndexError):
                    pass
            if positions:
                info["positions_wikidata"] = positions

        logger.info(f"[OK] Wikidata : {len(info)} champs pour {title}")
        return info

    except (URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[WARN] Wikidata indisponible pour {title}: {e}")
        return {}
    except Exception as e:
        logger.warning(f"[WARN] Erreur Wikidata pour {title}: {e}")
        return {}


def fetch_hatvp_info(name: str) -> dict:
    """Interroge l'API publique de la HATVP (Haute Autorite pour la Transparence
    de la Vie Publique) pour les declarations d'interets des responsables publics.
    
    Source officielle : https://www.hatvp.fr
    API ouverte conformement aux obligations de transparence.
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

        # Prendre la declaration la plus recente
        declaration = data[0]
        info = {
            "hatvp_declared": True,
            "hatvp_function": declaration.get("fonction", ""),
            "hatvp_url": f"https://www.hatvp.fr/consulter-les-declarations/?nom={quote(name)}"
        }

        logger.info(f"[OK] HATVP : declaration trouvee pour {name}")
        return info

    except (URLError, json.JSONDecodeError) as e:
        logger.debug(f"HATVP : pas de donnees pour {name} ({e})")
        return {}
    except Exception as e:
        logger.debug(f"HATVP : erreur pour {name} ({e})")
        return {}


def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        metadata = post.metadata
        title = metadata.get('title', metadata.get('nom_complet', metadata.get('nom', file_path.stem)))
        entity_type = metadata.get('type', 'Institution')

        # On saute si deja enrichi
        if 'wikipedia_enriched' in metadata:
            logger.info(f"[OK] {title} deja enrichi. Ignore.")
            return

        logger.info(f"Recherche Wikipedia pour : {title} ({entity_type})...")

        # 1. Recuperation du resume Wikipedia
        wiki_url = ""
        try:
            wiki_page = wikipedia.page(title, auto_suggest=False)
            wiki_summary = wiki_page.summary
            wiki_url = wiki_page.url
        except wikipedia.exceptions.PageError:
            logger.warning(f"[WARN] Page Wikipedia non trouvee pour {title}")
            wiki_summary = None
        except wikipedia.exceptions.DisambiguationError as e:
            logger.warning(f"[WARN] Page ambigue pour {title} : {e.options}")
            if not e.options:
                wiki_summary = None
            else:
                try:
                    wiki_page = wikipedia.page(e.options[0])
                    wiki_summary = wiki_page.summary
                    wiki_url = wiki_page.url
                except Exception:
                    wiki_summary = None

        # 2. Extraction de donnees (Mistral si disponible, sinon regex)
        if wiki_summary:
            extracted_data = extract_data_from_summary(wiki_summary, entity_type)
            if extracted_data:
                metadata.update(extracted_data)

        # 3. Enrichissement via Wikidata (donnees structurees complementaires)
        wikidata_info = fetch_wikidata_info(title)
        if wikidata_info:
            # Ne pas ecraser les donnees existantes, completer
            for key, value in wikidata_info.items():
                if key not in metadata or not metadata[key]:
                    metadata[key] = value

        # 4. Enrichissement HATVP (pour les personnes politiques francaises)
        if entity_type == "Personne":
            hatvp_info = fetch_hatvp_info(title)
            if hatvp_info:
                metadata.update(hatvp_info)

        # 5. Gestion des sources
        sources = metadata.get('sources', []) or []
        if not isinstance(sources, list):
            sources = [sources]
        if wiki_url and wiki_url not in sources:
            sources.append(wiki_url)
        if wikidata_info.get('wikidata_id'):
            wd_url = f"https://www.wikidata.org/wiki/{wikidata_info['wikidata_id']}"
            if wd_url not in sources:
                sources.append(wd_url)
        hatvp_url = metadata.get('hatvp_url', '')
        if hatvp_url and hatvp_url not in sources:
            sources.append(hatvp_url)
        metadata['sources'] = sources

        metadata['wikipedia_enriched'] = True

        # 6. Ecriture
        with open(file_path, 'wb') as f:
            frontmatter.dump(frontmatter.Post(content, **metadata), f)

        logger.info(f"[OK] {title} enrichi ({len(sources)} sources)")

    except Exception as e:
        logger.error(f"[FAIL] Erreur critique sur {file_path} : {e}", exc_info=True)

def main():
    logger.info("Lancement de l'enrichissement multi-sources (Wikipedia + Wikidata + HATVP)...")
    if llm.is_available():
        logger.info("[OK] Mistral disponible - extraction amelioree activee")
    else:
        logger.info("[WARN] Mistral indisponible - extraction par regex uniquement")
    
    # Cible uniquement les dossiers d'entites
    target_folders = ["personnes", "institutions", "companies", "ecoles", "medias", "think tanks"]
    
    md_files = []
    for folder in target_folders:
        if Path(folder).exists():
            md_files.extend(Path(folder).rglob("*.md"))
    
    # Also check for accented folder name
    if Path("écoles").exists():
        md_files.extend(Path("écoles").rglob("*.md"))
    
    total = len(md_files)
    for i, f in enumerate(md_files):
        logger.info(f"Processing {i + 1}/{total}...")
        process_file(f)
        # Delay between entities to avoid Wikipedia rate limits
        if i < total - 1:
            time.sleep(INTER_ENTITY_DELAY)
        
    logger.info("Enrichissement termine.")

if __name__ == "__main__":
    main()

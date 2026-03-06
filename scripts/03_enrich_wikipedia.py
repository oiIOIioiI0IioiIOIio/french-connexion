import sys
import os
import json
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

# Wikipedia en français
wikipedia.set_lang("fr")

# Timeout pour les requêtes HTTP externes
HTTP_TIMEOUT = 10

# Delay between processing each entity to avoid rate limits
INTER_ENTITY_DELAY = float(os.getenv("INTER_ENTITY_DELAY", "3.0"))


def get_schema_for_type(entity_type):
    """Définit les champs précis à extraire selon le type de fiche."""
    if entity_type == "Personne":
        return """
        {
          "birth_date": "Date de naissance (format YYYY-MM-DD ou texte simple)",
          "birth_place": "Lieu de naissance (Ville, Pays)",
          "nationality": "Nationalité",
          "occupation": "Profession ou rôle principal",
          "education": "Diplôme ou formation (alma_mater)",
          "website": "Site web officiel (URL)"
        }
        """
    elif entity_type in ["Institution", "Entreprise", "Ecole", "Media", "Fondation"]:
        return """
        {
          "founded": "Date de création ou fondation",
          "headquarters": "Ville ou pays du siège social",
          "leader": "Nom du dirigeant actuel (PDG, Président, Directeur)",
          "industry": "Secteur d'activité",
          "website": "Site web officiel (URL)"
        }
        """
    else:
        return "{}"


def fetch_wikidata_info(title: str) -> dict:
    """Récupère des données structurées depuis Wikidata via l'API publique.
    
    Wikidata fournit des métadonnées factuelles vérifiables : dates, identifiants
    officiels, liens vers d'autres bases de données institutionnelles.
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

        # P570 = date de décès
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

        # P39 = fonctions occupées (liste)
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

        logger.info(f"Wikidata : {len(info)} champs récupérés pour {title}")
        return info

    except (URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Wikidata indisponible pour {title}: {e}")
        return {}
    except Exception as e:
        logger.warning(f"Erreur Wikidata pour {title}: {e}")
        return {}


def fetch_hatvp_info(name: str) -> dict:
    """Interroge l'API publique de la HATVP (Haute Autorité pour la Transparence
    de la Vie Publique) pour les déclarations d'intérêts des responsables publics.
    
    Source officielle : https://www.hatvp.fr
    API ouverte conformément aux obligations de transparence.
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

        # Prendre la déclaration la plus récente
        declaration = data[0]
        info = {
            "hatvp_declared": True,
            "hatvp_function": declaration.get("fonction", ""),
            "hatvp_url": f"https://www.hatvp.fr/consulter-les-declarations/?nom={quote(name)}"
        }

        logger.info(f"HATVP : déclaration trouvée pour {name}")
        return info

    except (URLError, json.JSONDecodeError) as e:
        logger.debug(f"HATVP : pas de données pour {name} ({e})")
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

        # On saute si déjà enrichi
        if 'wikipedia_enriched' in metadata:
            logger.info(f"{title} déjà enrichi. Ignoré.")
            return

        logger.info(f"Recherche Wikipedia pour : {title} ({entity_type})...")

        # 1. Récupération du résumé Wikipedia
        wiki_url = ""
        try:
            wiki_page = wikipedia.page(title, auto_suggest=False)
            wiki_summary = wiki_page.summary
            wiki_url = wiki_page.url
        except wikipedia.exceptions.PageError:
            logger.warning(f"Page Wikipedia non trouvée pour {title}")
            wiki_summary = None
        except wikipedia.exceptions.DisambiguationError as e:
            logger.warning(f"Page ambiguë pour {title} : {e.options}")
            if not e.options:
                wiki_summary = None
            else:
                try:
                    wiki_page = wikipedia.page(e.options[0])
                    wiki_summary = wiki_page.summary
                    wiki_url = wiki_page.url
                except Exception:
                    wiki_summary = None

        # 2. Extraction précise via l'IA (si Wikipedia disponible)
        if wiki_summary:
            schema = get_schema_for_type(entity_type)
            extracted_data = llm.extract_yaml_data(wiki_summary, schema)
            if extracted_data:
                metadata.update(extracted_data)

        # 3. Enrichissement via Wikidata (données structurées complémentaires)
        wikidata_info = fetch_wikidata_info(title)
        if wikidata_info:
            # Ne pas écraser les données Wikipedia existantes, compléter
            for key, value in wikidata_info.items():
                if key not in metadata or not metadata[key]:
                    metadata[key] = value

        # 4. Enrichissement HATVP (pour les personnes politiques françaises)
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

        # 6. Écriture
        with open(file_path, 'wb') as f:
            frontmatter.dump(frontmatter.Post(content, **metadata), f)

        logger.info(f"{title} enrichi ({len(sources)} sources)")

    except Exception as e:
        logger.error(f"Erreur critique sur {file_path} : {e}", exc_info=True)

def main():
    logger.info("Lancement de l'enrichissement multi-sources (Wikipedia + Wikidata + HATVP)...")
    
    # Cible uniquement les dossiers d'entités
    target_folders = ["personnes", "institutions", "companies", "écoles", "medias", "think tanks"]
    
    md_files = []
    for folder in target_folders:
        if Path(folder).exists():
            md_files.extend(Path(folder).rglob("*.md"))
    
    total = len(md_files)
    for i, f in enumerate(md_files):
        logger.info(f"Processing {i + 1}/{total}...")
        process_file(f)
        # Delay between entities to avoid API rate limits
        if i < total - 1:
            time.sleep(INTER_ENTITY_DELAY)
        
    logger.info("Enrichissement terminé.")

if __name__ == "__main__":
    main()

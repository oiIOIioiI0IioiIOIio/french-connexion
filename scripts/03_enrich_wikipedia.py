import sys
import os
import wikipedia
import frontmatter
from pathlib import Path
from dotenv import load_dotenv

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

def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        metadata = post.metadata
        title = metadata.get('title', file_path.stem)
        entity_type = metadata.get('type', 'Institution')

        # On saute si déjà enrichi (optionnel, ici on vérifie un flag arbitraire ou juste le site web)
        if 'wikipedia_enriched' in metadata:
            logger.info(f"ℹ️ {title} déjà enrichi. Ignoré.")
            return

        logger.info(f"📖 Recherche Wikipedia pour : {title} ({entity_type})...")

        # 1. Récupération du résumé Wikipedia
        try:
            wiki_page = wikipedia.page(title, auto_suggest=False)
            wiki_summary = wiki_page.summary
        except wikipedia.exceptions.PageError:
            logger.warning(f"Page Wikipedia non trouvée pour {title}")
            return
        except wikipedia.exceptions.DisambiguationError as e:
            logger.warning(f"Page ambiguë pour {title} : {e.options}")
            # On essaie la première option suggérée
            if not e.options:
                return
            try:
                wiki_page = wikipedia.page(e.options[0])
                wiki_summary = wiki_page.summary
            except Exception:
                return

        # 2. Définition du schéma d'extraction
        schema = get_schema_for_type(entity_type)

        # 3. Extraction précise via l'IA
        extracted_data = llm.extract_yaml_data(wiki_summary, schema)

        if not extracted_data:
            logger.warning(f"Aucune donnée extraite pour {title}")
            return

        # 4. Mise à jour des métadonnées (Merge)
        # On ne veut pas écraser les données existantes importantes, sauf si on veut forcer la mise à jour
        # Ici on update simplement.
        metadata.update(extracted_data)
        metadata['wikipedia_enriched'] = True # Flag pour éviter de boucler

        # 5. Écriture (On remplace le fichier en gardant le contenu original)
        # Note: frontmatter.dump en mode 'wb' corrigé
        with open(file_path, 'wb') as f:
            frontmatter.dump(frontmatter.Post(content, **metadata), f)

        logger.info(f"✅ {title} mis à jour avec {len(extracted_data)} champs (ex: {list(extracted_data.keys())})")

    except Exception as e:
        logger.error(f"Erreur critique sur {file_path} : {e}", exc_info=True)

def main():
    logger.info("🚀 Lancement de l'enrichissement Wikipedia (Données précises uniquement)...")
    
    # Cible uniquement les dossiers d'entités
    target_folders = ["personnes", "institutions", "companies", "écoles", "medias", "think tanks"]
    
    md_files = []
    for folder in target_folders:
        if Path(folder).exists():
            md_files.extend(Path(folder).rglob("*.md"))
    
    for f in md_files:
        process_file(f)
        
    logger.info("🏁 Enrichissement terminé.")

if __name__ == "__main__":
    main()

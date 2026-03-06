import sys
import os
import yaml
import frontmatter
from pathlib import Path
import shutil
import time
from dotenv import load_dotenv  # Necessite : pip install python-dotenv

# Ajout du path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient

# Charger les variables d'environnement (pour la cle API)
load_dotenv()

logger = setup_logger()
git = GitHandler()
llm = MistralClient()

# Delay between processing each entity to avoid rate limits
INTER_ENTITY_DELAY = float(os.getenv("INTER_ENTITY_DELAY", "3.0"))

# CORRECTION ICI : L'indentation est fixee pour charger la config a l'interieur du bloc with
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        title = post.get('title', file_path.stem)

        logger.info(f"Analyse intelligente de : {title}...")

        # Liste des types valides depuis la configuration
        valid_types = list(CONFIG['entity_types'].keys())

        # 1. On lance la restructuration intelligente
        # Elle va decider du type toute seule en analysant le contenu reel
        default_template = "src/templates/personne.yaml"  # Fallback
        new_metadata = llm.intelligent_restructure(content, title, default_template, entity_types=valid_types)

        if not new_metadata:
            logger.error(f"Echec de l'analyse pour {title}")
            return

        # 2. Recuperation du type decide par l'IA
        entity_type = new_metadata.get('type', 'Institution')

        # Si le type n'est pas dans la config, on fallback sur Institution
        if entity_type not in CONFIG['entity_types']:
            logger.warning(f"Type '{entity_type}' inconnu, classe comme 'Institution'")
            entity_type = "Institution"

        config = CONFIG['entity_types'][entity_type]
        target_folder = Path(config['folder'])
        target_folder.mkdir(exist_ok=True, parents=True)

        # 3. Fusion des metadonnees : on preserve les donnees existantes
        #    et on met a jour selectivement avec les nouvelles informations
        final_metadata = dict(post.metadata)
        for key in ('type', 'summary', 'keywords'):
            if key in new_metadata:
                final_metadata[key] = new_metadata[key]
        # On s'assure que le type est correct
        final_metadata['type'] = entity_type

        # Preserver et enrichir les sources existantes
        existing_sources = final_metadata.get('sources', []) or []
        if not isinstance(existing_sources, list):
            existing_sources = [existing_sources]
        final_metadata['sources'] = existing_sources

        # 4. Ecriture
        new_post = frontmatter.Post(content, **final_metadata)

        new_path = target_folder / file_path.name
        if file_path != new_path:
            shutil.move(str(file_path), str(new_path))
            logger.info(f"Deplace vers {target_folder}")

        # CORRECTION ICI : Ouverture en mode binaire 'wb' pour eviter l'erreur "write() argument must be str, not bytes"
        with open(new_path, 'wb') as f:
            frontmatter.dump(new_post, f)

        logger.info(f"Succes : {title} structure en {entity_type}")

    except Exception as e:
        logger.error(f"Erreur critique sur {file_path} : {e}", exc_info=True)

def main():
    logger.info("Lancement du restructurateur autonome...")
    git.create_backup_tag()

    md_files = list(Path(".").rglob("*.md"))
    exclude_dirs = {".git", "scripts", "config", "admin"}
    md_files = [f for f in md_files
                if not any(part in exclude_dirs for part in f.parts)
                and f.name != "README.md"]

    total = len(md_files)
    for i, f in enumerate(md_files):
        logger.info(f"Processing {i + 1}/{total}...")
        process_file(f)
        # Delay between entities to avoid API rate limits
        if i < total - 1:
            time.sleep(INTER_ENTITY_DELAY)

    git.commit_changes("feat: restructuration intelligente et classification par IA")
    logger.info("Termine.")

if __name__ == "__main__":
    main()

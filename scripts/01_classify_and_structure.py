import sys
import os
import yaml
import frontmatter
from pathlib import Path
import shutil
import time
from dotenv import load_dotenv  # Nécessite : pip install python-dotenv

# Ajout du path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient

# Charger les variables d'environnement (pour la clé API)
load_dotenv()

logger = setup_logger()
git = GitHandler()
llm = MistralClient()

# Delay between processing each entity to avoid rate limits
INTER_ENTITY_DELAY = float(os.getenv("INTER_ENTITY_DELAY", "8.0"))

# Batch processing: process BATCH_SIZE entities, then take a BATCH_COOLDOWN break
try:
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))
    if BATCH_SIZE < 1:
        BATCH_SIZE = 5
except ValueError:
    BATCH_SIZE = 5

try:
    BATCH_COOLDOWN = float(os.getenv("BATCH_COOLDOWN", "30.0"))
    if BATCH_COOLDOWN < 0:
        BATCH_COOLDOWN = 30.0
except ValueError:
    BATCH_COOLDOWN = 30.0

# CORRECTION ICI : L'indentation est fixée pour charger la config à l'intérieur du bloc with
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

MIN_SUMMARY_LENGTH = 10


def is_already_processed(post):
    """Check if a file already has valid type, summary, and keywords.
    
    Returns True if the entity has been fully processed and does not need
    another API call. This avoids unnecessary rate-limited requests on re-runs.
    """
    metadata = post.metadata
    entity_type = metadata.get('type')
    summary = metadata.get('summary')
    keywords = metadata.get('keywords')

    # Must have a valid type from the config
    if not entity_type or entity_type not in CONFIG.get('entity_types', {}):
        return False

    # Must have a non-empty summary
    if not summary or not isinstance(summary, str) or len(summary.strip()) < MIN_SUMMARY_LENGTH:
        return False

    # Must have keywords (list with at least 1 entry)
    if not keywords or not isinstance(keywords, list) or len(keywords) < 1:
        return False

    return True

def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        title = post.get('title', file_path.stem)

        # Skip files that already have valid type, summary, and keywords
        if is_already_processed(post):
            logger.info(f"Déjà traité (type/summary/keywords présents) : {title} - ignoré")
            return False  # No API call made

        logger.info(f"Analyse intelligente de : {title}...")

        # Liste des types valides depuis la configuration
        valid_types = list(CONFIG['entity_types'].keys())

        # 1. On lance la restructuration intelligente
        # Elle va décider du type toute seule en analysant le contenu réel
        default_template = "src/templates/personne.yaml"  # Fallback
        new_metadata = llm.intelligent_restructure(content, title, default_template, entity_types=valid_types)

        if not new_metadata:
            logger.error(f"Échec de l'analyse pour {title}")
            return True  # API call was attempted

        # 2. Récupération du type décidé par l'IA
        entity_type = new_metadata.get('type', 'Institution')

        # Si le type n'est pas dans la config, on fallback sur Institution
        if entity_type not in CONFIG['entity_types']:
            logger.warning(f"Type '{entity_type}' inconnu, classé comme 'Institution'")
            entity_type = "Institution"

        config = CONFIG['entity_types'][entity_type]
        target_folder = Path(config['folder'])
        target_folder.mkdir(exist_ok=True, parents=True)

        # 3. Fusion des métadonnées : on préserve les données existantes
        #    et on met à jour sélectivement avec les nouvelles informations
        final_metadata = dict(post.metadata)
        for key in ('type', 'summary', 'keywords'):
            if key in new_metadata:
                final_metadata[key] = new_metadata[key]
        # On s'assure que le type est correct
        final_metadata['type'] = entity_type

        # Préserver et enrichir les sources existantes
        existing_sources = final_metadata.get('sources', []) or []
        if not isinstance(existing_sources, list):
            existing_sources = [existing_sources]
        final_metadata['sources'] = existing_sources

        # 4. Écriture
        new_post = frontmatter.Post(content, **final_metadata)

        new_path = target_folder / file_path.name
        if file_path != new_path:
            shutil.move(str(file_path), str(new_path))
            logger.info(f"Déplacé vers {target_folder}")

        # CORRECTION ICI : Ouverture en mode binaire 'wb' pour éviter l'erreur "write() argument must be str, not bytes"
        with open(new_path, 'wb') as f:
            frontmatter.dump(new_post, f)

        logger.info(f"Succès : {title} structuré en {entity_type}")
        return True  # API call was made

    except Exception as e:
        logger.error(f"Erreur critique sur {file_path} : {e}", exc_info=True)
        return True  # Assume API call was attempted

def main():
    logger.info("Lancement du restructurateur autonome...")
    git.create_backup_tag()

    md_files = list(Path(".").rglob("*.md"))
    exclude_dirs = {".git", "scripts", "config", "admin"}
    md_files = [f for f in md_files
                if not any(part in exclude_dirs for part in f.parts)
                and f.name != "README.md"]

    total = len(md_files)
    skipped = 0
    processed = 0
    api_calls_in_batch = 0

    for i, f in enumerate(md_files):
        logger.info(f"Processing {i + 1}/{total}...")
        made_api_call = process_file(f)

        if not made_api_call:
            skipped += 1
            continue

        processed += 1
        api_calls_in_batch += 1

        # Batch cooldown: after BATCH_SIZE API calls, take a longer break
        if api_calls_in_batch >= BATCH_SIZE and i < total - 1:
            logger.info(
                f"Batch de {BATCH_SIZE} appels API terminé - "
                f"pause de {BATCH_COOLDOWN:.0f}s (traités: {processed}, ignorés: {skipped})..."
            )
            time.sleep(BATCH_COOLDOWN)
            api_calls_in_batch = 0
        elif i < total - 1:
            # Normal inter-entity delay
            time.sleep(INTER_ENTITY_DELAY)

    logger.info(
        f"Terminé : {processed} traités, {skipped} ignorés (déjà à jour) sur {total} fichiers."
    )
    git.commit_changes("feat: restructuration intelligente et classification par IA")
    logger.info("Terminé.")

if __name__ == "__main__":
    main()

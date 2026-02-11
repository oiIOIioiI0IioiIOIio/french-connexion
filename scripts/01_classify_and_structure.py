import sys
import os
import yaml
import frontmatter
from pathlib import Path
import shutil
from dotenv import load_dotenv # Nécessite : pip install python-dotenv

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
   

with open("config/config.yaml", "r", encoding="utf-8") as f:
CONFIG = yaml.safe_load(f)

def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        title = post.get('title', file_path.stem)
        
        # Si le fichier a déjà un type, on peut choisir de le sauter ou de le mettre à jour
        # Ici on saute si 'type' est déjà présent pour aller plus vite
        if 'type' in post.metadata:
             logger.info(f"ℹ️ {title} déjà classé. Ignoré.")
             return

        logger.info(f"⚙️ Analyse intelligente de : {title}...")
        
        # 1. On lance la restructuration intelligente
        # Elle va décider du type toute seule en analysant le texte
        # On passe un template par défaut, l'IA ajustera
        default_template = "src/templates/personne.yaml" # Fallback
        new_metadata = llm.intelligent_restructure(content, title, default_template)
        
        if not new_metadata:
            logger.error(f"Échec de l'analyse pour {title}")
            return

        # 2. Récupération du type décidé par l'IA
        entity_type = new_metadata.get('type', 'Institution')
        
        # Si le type n'est pas dans la config, on fallback sur Institution
        if entity_type not in CONFIG['entity_types']:
             logger.warning(f"Type '{entity_type}' inconnu, classé comme 'Institution'")
             entity_type = "Institution"
             
        config = CONFIG['entity_types'][entity_type]
        target_folder = Path(config['folder'])
        target_folder.mkdir(exist_ok=True, parents=True)
        
        # 3. Fusion des métadonnées
        final_metadata = {**new_metadata}
        # On s'assure que le dossier est bon
        final_metadata['type'] = entity_type
        
        # 4. Écriture
        new_post = frontmatter.Post(content, **final_metadata)
        
        new_path = target_folder / file_path.name
        if file_path != new_path:
            shutil.move(str(file_path), str(new_path))
            logger.info(f"📁 Déplacé vers {target_folder}")
            
        with open(new_path, 'wb') as f:
            frontmatter.dump(new_post, f)
            
        logger.info(f"✨ Succès : {title} structuré en {entity_type}")

    except Exception as e:
        logger.error(f"Erreur critique sur {file_path} : {e}", exc_info=True)
def main():
    logger.info("🚀 Lancement du restructurateur autonome...")
    git.create_backup_tag()
    
    md_files = list(Path(".").rglob("*.md"))
    md_files = [f for f in md_files if ".git" not in str(f) and "scripts" not in str(f) and "config" not in str(f)]
    
    for f in md_files:
        process_file(f)
        
    git.commit_changes("feat: restructuration intelligente et classification par IA")
    logger.info("🏁 Terminé.")

if __name__ == "__main__":
    main()

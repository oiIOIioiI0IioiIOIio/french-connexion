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

def get_entity_config(text_content, title):
    """
    L'IA décide du type et retourne la config correspondante.
    """
    # On garde la classification légère pour économiser les tokens sur cette étape
    # ou on peut intégrer ça dans intelligent_restructure directement.
    # Ici, on fait une classification rapide.
    prompt = f"""
    Classe cette entité dans l'une de ces catégories : {list(CONFIG['entity_types'].keys())}.
    Titre : {title}
    Début du texte : {text_content[:500]}
    Réponds uniquement par le nom de la catégorie.
    """
    
    try:
        response = llm.client.chat(
            model="open-mistral-nemo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        category = response.choices[0].message.content.strip()
        # Fallback si la catégorie n'existe pas
        if category not in CONFIG['entity_types']:
            logger.warning(f"Catégorie inconnue '{category}', défaut sur 'Institution'")
            category = "Institution"
        return category
    except:
        return "Institution"

def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        title = post.get('title', file_path.stem)
        
        # Si le fichier a déjà un type et qu'on ne veut pas forcer la mise à jour, on peut skipper
        if 'type' in post.metadata and post.metadata.get('statut_note') == 'ok':
            logger.info(f"✅ {title} déjà verrouillé (statut: ok).")
            return

        logger.info(f"⚙️ Analyse intelligente de : {title}...")
        
        # 1. Classification
        entity_type = get_entity_config(content, title)
        config = CONFIG['entity_types'][entity_type]
        target_folder = Path(config['folder'])
        template_path = config['template']
        
        target_folder.mkdir(exist_ok=True, parents=True)
        
        # 2. Restructuration par l'IA (avec champs dynamiques)
        new_metadata = llm.intelligent_restructure(content, title, template_path)
        
        if not new_metadata:
            logger.error(f"Échec de la restructuration pour {title}")
            return

        # 3. Fusion intelligente : On garde les métadonnées existantes qui ne sont pas dans le nouveau template
        # pour ne pas perdre de champs personnalisés ajoutés manuellement précédemment.
        final_metadata = {**new_metadata} # On commence par les données IA
        for key, value in post.metadata.items():
            if key not in final_metadata:
                final_metadata[key] = value # On garde les anciennes customs keys
        
        # S'assurer que le type est bien défini
        final_metadata['type'] = entity_type
        
        # 4. Écriture
        new_post = frontmatter.Post(content, **final_metadata)
        
        # Déplacement si nécessaire
        new_path = target_folder / file_path.name
        if file_path != new_path:
            shutil.move(str(file_path), str(new_path))
            logger.info(f"📁 Déplacé vers {target_folder}")
            
        with open(new_path, 'w', encoding='utf-8') as f:
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

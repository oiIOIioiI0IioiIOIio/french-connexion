import sys
import os
import frontmatter
import wikipedia
import yaml
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient

logger = setup_logger()
git = GitHandler()
llm = MistralClient()
wikipedia.set_lang("fr") # Priorité au français

def enrich_file(file_path):
    post = frontmatter.load(file_path)
    meta = post.metadata
    
    # Si déjà enrichi, on saufe (idempotence)
    if meta.get('enriched_wiki'):
        return

    # Récupérer le nom à chercher
    search_term = meta.get('nom_complet', meta.get('nom', file_path.stem))
    
    try:
        # Recherche Wikipedia
        search_results = wikipedia.search(search_term, results=1)
        if not search_results:
            logger.info(f"❌ Pas de résultat Wiki pour {search_term}")
            return
            
        page = wikipedia.page(search_results[0])
        summary = page.summary[:1000] # Résumé pour l'IA
        
        logger.info(f"📖 Enrichissement de {search_term} via Wikipedia...")
        
        # Utiliser l'IA pour structurer les données Wiki dans le YAML
        extracted_yaml_str = llm.extract_yaml_data(summary, meta.get('type', 'Institution'))
        
        # Parser le YAML extrait
        extracted_data = yaml.safe_load(extracted_yaml_str)
        
        # Fusionner avec les métadonnées existantes
        for key, value in extracted_data.items():
            if key not in meta or not meta[key]: # Ne pas écraser si déjà rempli
                meta[key] = value
        
        meta['enriched_wiki'] = True
        meta['sources'] = meta.get('sources', [])
        meta['sources'].append({"type": "wikipedia", "titre": page.title, "url": page.url})
        
        # Sauvegarde
        with open(file_path, 'w', encoding='utf-8') as f:
            frontmatter.dump(post, f)
            
    except wikipedia.exceptions.PageError:
        logger.warning(f"Page Wikipedia introuvable pour {search_term}")
    except wikipedia.exceptions.DisambiguationError as e:
        logger.warning(f"Homonymie Wikipedia pour {search_term} : {e.options}")
    except Exception as e:
        logger.error(f"Erreur enrichissement {file_path} : {e}")

def main():
    logger.info("🧠 Démarrage de l'enrichissement Wikipedia...")
    
    md_files = list(Path(".").rglob("*.md"))
    for f in md_files:
        if ".git" in str(f) or "scripts" in str(f): continue
        enrich_file(f)
        
    git.commit_changes("feat: enrichissement automatique des données via Wikipedia")

if __name__ == "__main__":
    main()

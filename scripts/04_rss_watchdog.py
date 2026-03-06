import sys
import os
import feedparser
import yaml
import frontmatter
from pathlib import Path
from datetime import datetime
import subprocess

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient

logger = setup_logger()
git = GitHandler()
llm = MistralClient()

# Chargement config
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

def configure_git():
    """Configure Git user identity if not already set."""
    try:
        # Check if git user.name is set
        result = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True)
        if not result.stdout.strip():
            subprocess.run(['git', 'config', 'user.name', 'French Connexion Bot'], check=True)
            logger.info("Configuration Git user.name definie")
        
        # Check if git user.email is set
        result = subprocess.run(['git', 'config', 'user.email'], capture_output=True, text=True)
        if not result.stdout.strip():
            subprocess.run(['git', 'config', 'user.email', 'bot@french-connexion.local'], check=True)
            logger.info("Configuration Git user.email definie")
    except Exception as e:
        logger.warning(f"Impossible de configurer Git : {e}")

def process_feed(feed_url, keywords):
    """Lit un flux RSS et détecte les nouveaux articles."""
    feed = feedparser.parse(feed_url)
    
    # Safe check for feed title
    feed_title = getattr(feed.feed, 'title', 'Unknown Feed')
    logger.info(f"Lecture du flux : {feed_title}")
    
    for entry in feed.entries:
        title = entry.title
        summary = entry.get('summary', "")
        
        # Vérification des mots-clés
        if any(kw.lower() in title.lower() or kw.lower() in summary.lower() for kw in keywords):
            logger.info(f"Article pertinent detecte : {title}")
            extract_entities_and_create_draft(title, summary, entry.link)


def extract_entities_and_create_draft(title, content, url):
    """Utilise l'IA pour extraire les entités du texte et créer des brouillons."""
    try:
        # Use the proper retry-enabled method from the MistralClient
        entities_str = llm.extract_entities_for_rss(title, content)
        
        # Création des brouillons
        draft_folder = Path("00_Brouillons_RSS")
        draft_folder.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = draft_folder / f"{timestamp}_{title[:30].replace('/', '-')}.md"
        
        draft_content = f"""
---
type: draft
source_url: {url}
date_detection: {datetime.now().isoformat()}
entities_detected: {entities_str}
---

# {title}

{content}

*Note : Ce fichier a été généré automatiquement via le flux RSS. À valider et classer.*
"""
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(draft_content)
            
        logger.info(f"Brouillon cree : {filename.name}")
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction d'entités pour {title} : {e}")


def main():
    logger.info("Demarrage de la surveillance RSS...")
    
    # Configure Git before any commits
    configure_git()
    
    for feed_conf in CONFIG.get('rss_feeds', []):
        process_feed(feed_conf['url'], feed_conf['keywords'])
    
    # Try to commit, but don't fail if it doesn't work
    try:
        git.commit_changes("feat: ajout automatique de brouillons depuis RSS")
    except Exception as e:
        logger.warning(f"Commit Git non effectué : {e}")

if __name__ == "__main__":
    main()
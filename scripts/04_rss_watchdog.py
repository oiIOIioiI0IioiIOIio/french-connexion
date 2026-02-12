import logging
import subprocess
import os
import feedparser
from datetime import datetime
from pathlib import Path

# Imports LangChain (vérifiez que vos importations correspondent à votre version)
# L'erreur indique l'utilisation d'un objet 'Chat', probablement ChatOpenAI
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
except ImportError:
    from langchain.chat_models import ChatOpenAI
    from langchain.schema import HumanMessage

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("french_connection")

# Configuration
RSS_FEEDS = [
    "https://www.lemonde.fr/rss/une.xml",
    # Ajoutez d'autres flux ici si nécessaire
]
DRAFTS_DIR = Path("_drafts") # Répertoire cible pour les brouillons

def configure_git_identity():
    """
    Configure l'identité Git localement pour ce repository.
    Corrige l'erreur : 'Author identity unknown'
    """
    try:
        # Utilisation de l'identité du bot GitHub Actions
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        logger.info("Identité Git configurée avec succès.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Impossible de configurer l'identité Git : {e}")

def extract_entities(text):
    """
    Extrait les entités en utilisant le modèle LLM.
    Corrige l'erreur : 'Chat' object has no attribute 'completions'
    """
    try:
        # Initialisation du modèle (ChatOpenAI)
        # Assurez-vous que la clé API est définie dans les variables d'environnement (OPENAI_API_KEY)
        llm = ChatOpenAI(temperature=0, model_name="gpt-4") # ou gpt-3.5-turbo

        prompt = f"Extrait les entités nommées (personnes, lieux, organisations) du texte suivant : {text}"
        
        # CORRECTION ICI : Utiliser .invoke() au lieu de .completions.create()
        # L'objet ChatOpenAI s'utilise via la méthode invoke() ou l'opérateur ()
        response = llm.invoke([HumanMessage(content=prompt)])
        
        return response.content
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction d'entités pour '{text[:50]}...' : {e}")
        return None

def process_feed(url):
    logger.info(f"📡 Lecture du flux : {url}")
    feed = feedparser.parse(url)
    feed_title = feed.feed.get('title', 'Unknown Feed')
    logger.info(f"📡 Lecture du flux : {feed_title}")

    for entry in feed.entries:
        # Logique de filtrage (exemple fictif basé sur les logs)
        # Dans votre script original, cette logique détermine si l'article est "pertinent"
        title = entry.get('title', '')
        link = entry.get('link', '')
        
        # Exemple de détection simple (à adapter selon votre logique métier réelle)
        if "Macron" in title or "France" in title or "ONU" in title:
            logger.info(f"🎯 Article pertinent détecté : {title}")
            
            # Extraction des entités
            entities = extract_entities(title)
            logger.info(f"   Entités trouvées : {entities}")

            # Création du fichier brouillon
            if entities:
                safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).rstrip()
                filename = DRAFTS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-{safe_title[:50]}.md"
                
                if not filename.exists():
                    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"# {title}\n\n")
                        f.write(f"**Source :** {link}\n\n")
                        f.write(f"**Entités :** {entities}\n\n")
                        f.write(f"**Date :** {entry.get('published', 'N/A')}\n")
                    logger.info(f"   Brouillon créé : {filename}")

def commit_changes():
    try:
        # 1. Configurer l'identité AVANT le commit
        configure_git_identity()

        # 2. Ajouter les fichiers
        subprocess.run(["git", "add", "."], check=True)
        
        # 3. Commiter
        commit_msg = f"chore: ajout de brouillons depuis RSS"
        # Vérification si y'a des changements à commiter
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if result.returncode != 0:
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            
            # 4. Push (optionnel, selon vos besoins)
            subprocess.run(["git", "push"], check=True)
            logger.info("Changements commités et poussés avec succès.")
        else:
            logger.info("Aucun nouveau changement à commiter.")

    except subprocess.CalledProcessError as e:
        logger.error(f"Erreur Git : {e}")
    except Exception as e:
        logger.error(f"Erreur inattendue lors du commit : {e}")

if __name__ == "__main__":
    logger.info("👁️ Démarrage de la surveillance RSS...")
    
    for feed_url in RSS_FEEDS:
        try:
            process_feed(feed_url)
        except Exception as e:
            logger.error(f"Erreur lors du traitement du flux {feed_url} : {e}")
    
    # Commiter les résultats à la fin
    commit_changes()

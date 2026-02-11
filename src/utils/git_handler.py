import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("french_connection")

class GitHandler:
    def __init__(self, repo_path="."):
        self.repo_path = Path(repo_path)

    def commit_changes(self, message):
        """Crée un commit git avec les modifications actuelles."""
        try:
            subprocess.run(["git", "-C", self.repo_path, "add", "."], check=True)
            # Vérifier s'il y a des changements à commiter
            result = subprocess.run(["git", "-C", self.repo_path, "diff", "--cached", "--quiet"], check=False)
            if result.returncode != 0:
                subprocess.run(["git", "-C", self.repo_path, "commit", "-m", message], check=True)
                logger.info(f"Git commit réussi : {message}")
                return True
            else:
                logger.info("Aucun changement à commiter.")
                return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Erreur Git lors du commit : {e}")
            return False

    def create_backup_tag(self):
        """Crée un tag de sauvegarde avant modifications lourdes."""
        import datetime
        tag_name = f"backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            subprocess.run(["git", "-C", self.repo_path, "tag", tag_name], check=True)
            logger.info(f"Tag de sauvegarde créé : {tag_name}")
        except subprocess.CalledProcessError:
            logger.warning("Impossible de créer le tag de sauvegarde.")

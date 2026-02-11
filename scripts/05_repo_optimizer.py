import sys
import os
import frontmatter
import yaml
from pathlib import Path
from collections import Counter

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

logger = setup_logger()
git = GitHandler()

def analyze_field_consistency(entity_type):
    """
    Analyse toutes les fiches d'un type pour voir quels champs sont utilisés.
    """
    md_files = list(Path(f"{CONFIG['entity_types'][entity_type]['folder']}").rglob("*.md"))
    field_usage = Counter()
    
    for f in md_files:
        post = frontmatter.load(f)
        for key in post.metadata.keys():
            field_usage[key] += 1
            
    logger.info(f"Analyse des champs pour '{entity_type}' :")
    for field, count in field_usage.most_common():
        logger.info(f" - {field}: utilisé dans {count} fiches")

    # Ici, on pourrait appeler l'IA pour dire : 
    # "Voici les champs trouvés : [...] . Certains semblent être des doublons (ex: education/formation). 
    # Propose un schéma unifié."

def auto_standardize(entity_type, mapping_rules):
    """
    Applique des règles de renommage de champs automatiquement.
    mapping_rules = {'old_key': 'new_key'}
    """
    folder = Path(f"{CONFIG['entity_types'][entity_type]['folder']}")
    md_files = list(folder.rglob("*.md"))
    
    for f in md_files:
        post = frontmatter.load(f)
        modified = False
        new_meta = post.metadata.copy()
        
        for old_key, new_key in mapping_rules.items():
            if old_key in new_meta:
                # Fusion ou remplacement ?
                # Ici simple renommage si la nouvelle clé n'existe pas
                if new_key not in new_meta:
                    new_meta[new_key] = new_meta.pop(old_key)
                    modified = True
                    logger.info(f"Renommage {old_key} -> {new_key} dans {f.name}")
        
        if modified:
            new_post = frontmatter.Post(post.content, **new_meta)
            with open(f, 'w', encoding='utf-8') as file:
                frontmatter.dump(new_post, file)
                
    git.commit_changes(f"chore: standardisation automatique des champs pour {entity_type}")

# Exemple d'utilisation (à intégrer dans le main si besoin)
if __name__ == "__main__":
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        CONFIG = yaml.safe_load(f)
        
    # Analyse des personnes
    analyze_field_consistency("Personne")
    
    # Exemple de règle manuelle pour l'instant, mais l'IA pourrait les générer
    rules = {"education": "formation", "job": "carriere"}
    # auto_standardize("Personne", rules)

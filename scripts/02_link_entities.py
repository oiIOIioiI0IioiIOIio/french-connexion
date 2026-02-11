import sys
import os
import frontmatter
from pathlib import Path
import spacy # Utilisation de Spacy local pour la rapidité, on peut utiliser HF API aussi
import yaml

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

logger = setup_logger()
git = GitHandler()

# Charger le modèle NER Français (à faire: python -m spacy download fr_core_news_lg)
try:
    nlp = spacy.load("fr_core_news_lg")
except OSError:
    logger.error("Modèle Spacy manquant. Lancez: python -m spacy download fr_core_news_lg")
    sys.exit(1)

# Index de toutes les entités connues pour le linking
# Format: { "nom_normalisé": "chemin/vers/fichier.md" }
ENTITY_INDEX = {}

def build_entity_index():
    """Construit un index de toutes les entités existantes pour le linking."""
    logger.info("🔍 Construction de l'index des entités...")
    md_files = list(Path(".").rglob("*.md"))
    for f in md_files:
        if ".git" in str(f): continue
        post = frontmatter.load(f)
        name = post.get('nom_complet', post.get('nom', f.stem))
        # Normalisation simple (minuscule, sans accents)
        norm_name = name.lower().replace(" ", "_").replace("-", "_")
        ENTITY_INDEX[norm_name] = str(f.relative_to(Path(".")))

def link_document(file_path):
    """Parcourt un document et crée des liens wiki [[...]] vers d'autres entités."""
    post = frontmatter.load(file_path)
    content = post.content
    doc = nlp(content)
    
    modified = False
    
    # Parcourir les entités nommées (PERSON, ORG)
    for ent in doc.ents:
        if ent.label_ in ["PERSON", "ORG"]:
            text = ent.text
            norm_text = text.lower().replace(" ", "_").replace("-", "_")
            
            # Vérification si l'entité existe dans notre index
            # On peut implémenter un scoring de similarité ici
            if norm_text in ENTITY_INDEX:
                target_file = ENTITY_INDEX[norm_text]
                
                # Créer le lien Obsidian
                wiki_link = f"[[{text}]]"
                
                # Remplacer dans le texte (simple str.replace, attention au contexte)
                # Pour éviter les boucles infinies ou les remplacements partiels, on fait attention
                if wiki_link not in content:
                    content = content.replace(text, wiki_link, 1) # Remplacer la première occurrence
                    modified = True
                    logger.debug(f"🔗 Lien créé : {text} -> {target_file}")

    if modified:
        post.content = content
        with open(file_path, 'w', encoding='utf-8') as f:
            frontmatter.dump(post, f)

def main():
    build_entity_index()
    
    # Parcourir tous les fichiers pour créer les liens
    md_files = list(Path(".").rglob("*.md"))
    for f in md_files:
        if ".git" in str(f) or "scripts" in str(f): continue
        link_document(f)
        
    git.commit_changes("feat: génération automatique des liens et backlinks")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Script pour mettre à jour automatiquement les imports de MistralClient
vers MistralAIClient dans tous les fichiers Python du projet.
"""

import os
import re
from pathlib import Path

def update_imports(file_path):
    """Met à jour les imports dans un fichier."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Pattern 1: from src.utils.llm_client import MistralClient
        content = re.sub(
            r'from\s+src\.utils\.llm_client\s+import\s+MistralClient\b',
            'from src.utils.llm_client import MistralAIClient',
            content
        )
        
        # Pattern 2: import de MistralClient avec alias
        content = re.sub(
            r'from\s+src\.utils\.llm_client\s+import\s+MistralClient\s+as\s+(\w+)',
            r'from src.utils.llm_client import MistralAIClient as \1',
            content
        )
        
        # Pattern 3: Utilisation de la classe (simple)
        # Attention: ne pas remplacer dans les strings ou commentaires
        # On cible spécifiquement les instanciations
        content = re.sub(
            r'\bMistralClient\s*\(',
            'MistralAIClient(',
            content
        )
        
        if content != original_content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
        
    except Exception as e:
        print(f"❌ Erreur avec {file_path}: {e}")
        return False

def scan_and_update(root_dir='.'):
    """Scanne tous les fichiers Python et met à jour les imports."""
    root_path = Path(root_dir)
    updated_files = []
    skipped_files = []
    
    # Fichiers à exclure
    exclude_patterns = {
        'venv', '.venv', 'env', '__pycache__', '.git', 
        'node_modules', 'dist', 'build'
    }
    
    print("🔍 Scan des fichiers Python...\n")
    
    for py_file in root_path.rglob('*.py'):
        # Vérifier si le fichier est dans un dossier exclu
        if any(part in exclude_patterns for part in py_file.parts):
            continue
        
        # Ne pas modifier llm_client.py lui-même
        if py_file.name == 'llm_client.py':
            print(f"⏭️  Ignoré (fichier source) : {py_file}")
            skipped_files.append(py_file)
            continue
        
        print(f"📄 Vérification : {py_file}")
        
        # Vérifier si le fichier contient MistralClient
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if 'MistralClient' not in content:
                    continue
        except Exception as e:
            print(f"   ⚠️  Erreur de lecture : {e}")
            continue
        
        if update_imports(py_file):
            print(f"   ✅ Mis à jour")
            updated_files.append(py_file)
        else:
            print(f"   ℹ️  Aucun changement nécessaire")
    
    # Rapport
    print("\n" + "="*50)
    print("📊 RAPPORT")
    print("="*50)
    print(f"✅ Fichiers mis à jour : {len(updated_files)}")
    print(f"⏭️  Fichiers ignorés : {len(skipped_files)}")
    
    if updated_files:
        print("\n📝 Fichiers modifiés :")
        for f in updated_files:
            print(f"   - {f}")
    
    print("\n⚠️  IMPORTANT :")
    print("   1. Vérifiez manuellement les changements avec 'git diff'")
    print("   2. Testez votre code avant de committer")
    print("   3. Certains cas complexes peuvent nécessiter une modification manuelle")

if __name__ == "__main__":
    import sys
    
    # Vérification de sécurité
    print("⚠️  Ce script va modifier vos fichiers Python !")
    print("   Assurez-vous d'avoir une sauvegarde ou que vos changements sont committés.")
    
    response = input("\n   Continuer ? (oui/non) : ").strip().lower()
    
    if response in ['oui', 'yes', 'y', 'o']:
        root = sys.argv[1] if len(sys.argv) > 1 else '.'
        scan_and_update(root)
    else:
        print("\n❌ Annulé par l'utilisateur")

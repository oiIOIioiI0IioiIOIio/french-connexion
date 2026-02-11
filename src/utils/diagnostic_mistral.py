#!/usr/bin/env python3
"""
Script de diagnostic pour vérifier l'installation du SDK Mistral AI
"""

import sys

print("=== DIAGNOSTIC MISTRAL AI SDK ===\n")

# Test 1: Vérifier si mistralai est installé
try:
    import mistralai
    print("✅ Package 'mistralai' installé")
    
    # Essayer de récupérer la version
    try:
        version = mistralai.__version__
        print(f"   Version : {version}")
    except AttributeError:
        print("   ⚠️  Impossible de déterminer la version")
    
except ImportError:
    print("❌ Package 'mistralai' NON installé")
    print("\nInstallez-le avec :")
    print("   pip install mistralai")
    sys.exit(1)

# Test 2: Vérifier l'API v1.0+ (nouvelle)
print("\n--- Test API v1.0+ ---")
try:
    from mistralai import Mistral, ChatMessage
    print("✅ API v1.0+ disponible (Mistral, ChatMessage)")
    print("   Recommandé : Utilisez cette version")
except ImportError as e:
    print(f"❌ API v1.0+ non disponible")
    print(f"   Détails : {e}")

# Test 3: Vérifier l'API v0.x (ancienne)
print("\n--- Test API v0.x ---")
try:
    from mistralai.client import MistralClient
    print("✅ API v0.x disponible (MistralClient)")
    print("   Note : Version ancienne, considérez une mise à jour")
    
    # Test différents chemins d'import pour ChatMessage
    try:
        from mistralai.models.chat_completion import ChatMessage
        print("   ✅ ChatMessage importable depuis mistralai.models.chat_completion")
    except ImportError:
        try:
            from mistralai.models.models import ChatMessage
            print("   ✅ ChatMessage importable depuis mistralai.models.models")
        except ImportError:
            print("   ⚠️  ChatMessage non trouvé dans les emplacements habituels")
            
except ImportError as e:
    print(f"❌ API v0.x non disponible")
    print(f"   Détails : {e}")

# Test 4: Liste des modules disponibles
print("\n--- Contenu du package mistralai ---")
try:
    import mistralai
    contents = dir(mistralai)
    important_items = [item for item in contents if not item.startswith('_')]
    print(f"Exports disponibles : {', '.join(important_items)}")
except Exception as e:
    print(f"Erreur lors de l'inspection : {e}")

# Recommandations
print("\n=== RECOMMANDATIONS ===")
try:
    from mistralai import Mistral
    print("✅ Votre installation est à jour (v1.0+)")
    print("   Aucune action requise")
except ImportError:
    try:
        from mistralai.client import MistralClient
        print("⚠️  Vous utilisez une version ancienne (v0.x)")
        print("   Recommandation : Mettez à jour avec :")
        print("   pip install --upgrade mistralai")
    except ImportError:
        print("❌ Installation corrompue ou incomplète")
        print("   Recommandation : Réinstallez :")
        print("   pip uninstall mistralai")
        print("   pip install mistralai")

print("\n=== FIN DU DIAGNOSTIC ===")

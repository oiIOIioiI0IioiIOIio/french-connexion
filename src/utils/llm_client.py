import os
import yaml
import logging

# --- GESTION DES VERSIONS AUTO-ADAPTATIVE ---
# On essaie d'abord d'importer la nouvelle API (Mistral SDK >= 1.0.0)
try:
    from mistralai import Mistral
    from mistralai import ChatMessage
    SDK_VERSION = "v1"
    MistralSDK = Mistral  # Alias pour utilisation dans __init__
    logging.info("🤖 Mistral AI SDK v1.0+ détecté.")
except ImportError:
    # Si ça échoue, on utilise l'ancienne API (Mistral SDK < 1.0.0)
    try:
        from mistralai.client import MistralClient as MistralSDK  # Import avec alias
        from mistralai.models.chat_completion import ChatMessage
        SDK_VERSION = "v0"
        logging.warning("⚠️ Mistral AI SDK v0.x détecté. Utilisation du mode compatibilité.")
    except ImportError as e:
        raise ImportError(
            "ERREUR CRITIQUE : La bibliothèque 'mistralai' n'est pas installée correctement.\n"
            f"Détails : {e}\n"
            "Installez-la avec : pip install mistralai"
        )

logger = logging.getLogger("french_connection")


class MistralAIClient:
    """
    Client Wrapper compatible avec les versions 0.x et 1.0+ de Mistral AI.
    Renommé de MistralClient vers MistralAIClient pour éviter les conflits.
    """
    
    def __init__(self):
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError(
                "ERREUR CRITIQUE : MISTRAL_API_KEY n'est pas définie.\n"
                "Vérifiez les Secrets GitHub Actions."
            )

        self.model = os.getenv("MISTRAL_MODEL", "open-mistral-nemo")

        # Initialisation selon la version détectée
        self.client = MistralSDK(api_key=api_key)
        logger.info(f"Client Mistral initialisé (SDK {SDK_VERSION}, modèle: {self.model})")

    def load_template(self, template_path):
        """Charge le template YAML."""
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"Template non trouvé : {template_path}")
            return "type: entity\n"

    def intelligent_restructure(self, text_content, title, template_path):
        """
        Analyse et restructure le contenu.
        Gère les différences d'appel API entre v0 et v1.
        """
        template_content = self.load_template(template_path)
        
        system_prompt = f"""Tu es un expert en gestion de bases de connaissances pour le projet "French Connection".

TA MISSION :
1. Analyser le texte fourni (fiche brute).
2. Remplir rigoureusement les champs du template YAML fourni ci-dessous.
3. Si tu détectes des informations pertinentes qui ne correspondent à aucun champ existant, TU DOIS ajouter de nouvelles clés au YAML (en snake_case).
4. Normalise les données : 
   - Dates en format ISO (YYYY-MM-DD).
   - Listes avec tirets.
   - Noms d'entités en liens wikilinks [[Nom]].

CONSIGNE DE SÉCURITÉ : Ne jamais inventer d'information. Si une donnée est absente, laisse le champ vide ou la liste vide [].

TEMPLATE À RESPECTER (et étendre si besoin) :
{template_content}
"""

        user_message = f"Titre de la fiche : {title}\n\nContenu brut à analyser :\n{text_content}"

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_message)
        ]

        try:
            # --- APPEL API ADAPTATIF ---
            if SDK_VERSION == "v1":
                # Syntaxe Nouvelle (v1.0+)
                response = self.client.chat.complete(
                    model=self.model,
                    messages=messages,
                    temperature=0.2
                )
            else:
                # Syntaxe Ancienne (v0.x)
                response = self.client.chat(
                    model=self.model,
                    messages=messages,
                    temperature=0.2
                )

            # Parsing de la réponse (Structure identique dans v0 et v1)
            if not response.choices or not response.choices[0].message:
                logger.error(f"Réponse vide de l'IA pour {title}")
                return None

            yaml_str = response.choices[0].message.content
            
            # Nettoyage robuste
            if "```yaml" in yaml_str:
                yaml_str = yaml_str.split("```yaml")[1].split("```")[0].strip()
            elif "```" in yaml_str:
                yaml_str = yaml_str.split("```")[1].split("```")[0].strip()

            try:
                data = yaml.safe_load(yaml_str)
                if not isinstance(data, dict):
                    logger.error(f"Format invalide retourné par l'IA pour {title}")
                    return None
                return data 
            except yaml.YAMLError as e:
                logger.error(f"Erreur parsing YAML pour {title}: {e}")
                return None
                    
        except Exception as e:
            logger.error(f"Erreur critique IA pour {title} : {e}", exc_info=True)
            return None

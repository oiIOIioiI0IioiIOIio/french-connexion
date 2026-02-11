import os
from mistralai import Mistral
from src.utils.logger import setup_logger

logger = setup_logger()

class MistralClient:
    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY n'est pas définie dans les variables d'environnement.")
        
        # Initialisation du client (Nouvelle syntaxe v1)
        self.client = Mistral(api_key=api_key)
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest") # Utilise un modèle récent par défaut

    def intelligent_restructure(self, content: str, title: str, template_path: str) -> dict:
        """
        Analyse le contenu et renvoie les métadonnées structurées (type, résumé, etc.)
        """
        logger.info(f"Appel à l'API Mistral pour structurer : {title}")
        
        # Construction du prompt système
        system_prompt = f"""
        Tu es un assistant expert en analyse de documents. Ton rôle est de structurer l'information fournie.
        Tu dois renvoyer UNIQUEMENT un objet JSON valide avec les clés suivantes :
        - "type" : Le type de l'entité (parmi : Personne, Institution, Evenement, Concept).
        - "summary" : Un résumé en 2 phrases.
        - "keywords" : Une liste de 5 mots-clés pertinents.
        """

        try:
            # Appel API avec la nouvelle syntaxe (chat.completions.create)
            chat_response = self.client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Titre : {title}\n\nContenu :\n{content}"}
                ],
                response_format={"type": "json_object"} # Force la réponse en JSON
            )

            # Récupération du contenu
            if chat_response.choices and chat_response.choices[0].message:
                content_text = chat_response.choices[0].message.content
                import json
                return json.loads(content_text)
            else:
                logger.error("Réponse vide de l'API Mistral")
                return {}

        except Exception as e:
            logger.error(f"Erreur lors de l'appel à l'API Mistral : {e}")
            return {}

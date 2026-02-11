import os
import json
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
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")

    def intelligent_restructure(self, content: str, title: str, template_path: str) -> dict:
        """Analyse le contenu et renvoie les métadonnées structurées (type, résumé, etc.)."""
        logger.info(f"Appel à l'API Mistral pour structurer : {title}")
        
        system_prompt = """
        Tu es un assistant expert en analyse de documents. Ton rôle est de structurer l'information fournie.
        Tu dois renvoyer UNIQUEMENT un objet JSON valide avec les clés suivantes :
        - "type" : Le type de l'entité (parmi : Personne, Institution, Evenement, Concept).
        - "summary" : Un résumé en 2 phrases.
        - "keywords" : Une liste de 5 mots-clés pertinents.
        """

        try:
            chat_response = self.client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Titre : {title}\n\nContenu :\n{content}"}
                ],
                response_format={"type": "json_object"}
            )
            if chat_response.choices and chat_response.choices[0].message:
                return json.loads(chat_response.choices[0].message.content)
            return {}
        except Exception as e:
            logger.error(f"Erreur lors de l'appel à l'API Mistral : {e}")
            return {}

    def extract_yaml_data(self, text: str, schema_description: str) -> dict:
        """
        Extrait des données précises (métadonnées) depuis un texte brut (ex: Wikipedia)
        en suivant un schéma strict fourni en prompt. Ne génère pas de texte narratif.
        """
        logger.info("Appel à l'API Mistral pour extraire des données précises (ex: dates, lieux)...")
        
        system_prompt = f"""
        Tu es un extracteur de données métier. Ton unique but est d'extraire des informations factuelles précises du texte fourni.
        
        CONSIGNES STRICTES :
        1. Renvoie UNIQUEMENT un objet JSON valide.
        2. Ne rédige aucune phrase.
        3. N'inclus pas de champs si l'information n'est pas dans le texte.
        4. Respecte ce format de sortie (schéma) :
        
        {schema_description}
        """

        try:
            chat_response = self.client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Texte source (Wikipedia) :\n\n{text}"}
                ],
                response_format={"type": "json_object"}
            )

            if chat_response.choices and chat_response.choices[0].message:
                return json.loads(chat_response.choices[0].message.content)
            return {}
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction de données : {e}")
            return {}

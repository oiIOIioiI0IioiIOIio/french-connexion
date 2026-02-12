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

    def intelligent_restructure(self, content: str, title: str, template_path: str, entity_types: list = None) -> dict:
        """Analyse le contenu et renvoie les métadonnées structurées (type, résumé, etc.).
        
        Args:
            content: Le contenu Markdown de la fiche.
            title: Le titre de l'entité.
            template_path: Chemin vers le template YAML (fallback).
            entity_types: Liste des types d'entités valides issus de la configuration.
        """
        logger.info(f"Appel à l'API Mistral pour structurer : {title}")

        if entity_types is None:
            entity_types = ["Personne", "Entreprise", "Institution", "Ecole", "Media", "Fondation", "Parti"]

        types_list = ", ".join(entity_types)
        
        system_prompt = f"""
Tu es un assistant expert en classification d'entités du réseau d'influence français.
Ton rôle est d'analyser le contenu d'une fiche et de déterminer précisément le type d'entité décrite.

TYPES DISPONIBLES (choisis EXACTEMENT l'un de ces types) : {types_list}

RÈGLES DE CLASSIFICATION :
- "Personne" : individu, personnalité politique, chef d'entreprise, intellectuel, artiste, etc.
- "Entreprise" : société commerciale, groupe industriel, holding, banque, compagnie (ex: LVMH, Air France, BNP Paribas, Rothschild & Co).
- "Institution" : organisme public, administration, organisation internationale, club privé, cercle, association (ex: Conseil d'État, ONU, Cercle de l'Union).
- "Ecole" : établissement d'enseignement, université, grande école, lycée, académie scolaire (ex: ENA, Sciences Po, HEC, Polytechnique, Lycée du Parc).
- "Media" : chaîne de télévision, radio, journal, magazine, agence de presse, média en ligne (ex: BFM TV, Le Monde, AFP, CNews, Canal+).
- "Fondation" : think tank, fondation, institut de recherche ou de réflexion, centre d'analyse (ex: Institut Montaigne, Brookings, Terra Nova, Aspen Institute).
- "Parti" : parti politique, mouvement politique, formation politique (ex: Les Républicains, Renaissance, Rassemblement National).

IMPORTANT : Base ta classification sur le CONTENU RÉEL de la fiche, pas sur le type existant dans les métadonnées.
Par exemple, une fiche décrivant une chaîne de télévision doit être classée "Media" même si son type actuel est "Institution".

Renvoie UNIQUEMENT un objet JSON valide avec les clés suivantes :
- "type" : Le type de l'entité (EXACTEMENT l'un des types listés ci-dessus).
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

    def extract_entities_for_rss(self, title: str, summary: str) -> str:
        """
        Méthode utilitaire simple pour extraire les entités (personnes / organisations)
        à partir d'un titre et d'un résumé. Retourne la cha��ne brute renvoyée par le modèle
        (idéalement une liste JSON comme ['Nom1','Nom2']).
        """
        logger.info(f"Appel à l'API Mistral pour extraire des entités : {title}")

        prompt = f"""
        Analyse ce titre et ce résumé d'article de presse.
        Extrais les noms des personnes ou organisations importantes (élites, dirigeants).
        Si tu en trouves, retourne-les sous forme de liste JSON : ["Nom1", "Nom2"].
        Si rien d'intéressant, retourne [].

        Titre: {title}
        Résumé: {summary}
        """

        try:
            chat_response = self.client.chat.complete(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "text"}
            )
            # Return raw content if available
            if chat_response.choices and chat_response.choices[0].message:
                return chat_response.choices[0].message.content
            # Fallback for dict-like response
            if isinstance(chat_response, dict):
                choices = chat_response.get('choices') or []
                if choices:
                    first = choices[0]
                    if isinstance(first, dict):
                        msg = first.get('message') or {}
                        return msg.get('content') or first.get('content') or str(first)
            return str(chat_response)
        except Exception as e:
            logger.error(f"Erreur lors de l'appel Mistral pour les entités RSS : {e}")
            return '[]'
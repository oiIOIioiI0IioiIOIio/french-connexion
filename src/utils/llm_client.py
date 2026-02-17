import os
import json
import time
from mistralai import Mistral, SDKError
from src.utils.logger import setup_logger

logger = setup_logger()

# Configuration du retry avec backoff exponentiel pour les erreurs 429
try:
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
    if MAX_RETRIES < 1:
        logger.warning(f"MAX_RETRIES must be >= 1, using default of 5")
        MAX_RETRIES = 5
except ValueError:
    logger.warning(f"Invalid MAX_RETRIES value, using default of 5")
    MAX_RETRIES = 5

try:
    RETRY_BASE_DELAY = int(os.getenv("RETRY_BASE_DELAY", "2"))
    if RETRY_BASE_DELAY < 1:
        logger.warning(f"RETRY_BASE_DELAY must be >= 1, using default of 2")
        RETRY_BASE_DELAY = 2
except ValueError:
    logger.warning(f"Invalid RETRY_BASE_DELAY value, using default of 2")
    RETRY_BASE_DELAY = 2

try:
    RETRY_MAX_DELAY = int(os.getenv("RETRY_MAX_DELAY", "60"))
    if RETRY_MAX_DELAY < RETRY_BASE_DELAY:
        logger.warning(f"RETRY_MAX_DELAY must be >= RETRY_BASE_DELAY, using default of 60")
        RETRY_MAX_DELAY = 60
except ValueError:
    logger.warning(f"Invalid RETRY_MAX_DELAY value, using default of 60")
    RETRY_MAX_DELAY = 60

# Keywords for detecting transient errors in error messages
TRANSIENT_ERROR_KEYWORDS = ['timeout', 'connection', 'network', 'temporary', 'unavailable']

class MistralClient:
    def __init__(self):
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY n'est pas définie dans les variables d'environnement.")
        
        # Initialisation du client (Nouvelle syntaxe v1)
        self.client = Mistral(api_key=api_key)
        self.model = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    
    def _is_valid_response(self, response):
        """Check if response has valid structure with choices."""
        return (response and 
                hasattr(response, 'choices') and 
                response.choices and 
                len(response.choices) > 0)

    def _chat_complete_with_retry(self, **call_params):
        """
        🔄 Wrapper pour self.client.chat.complete() avec retry et backoff exponentiel
        pour gérer les erreurs 429 (Rate Limited) de l'API Mistral.
        """
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.chat.complete(**call_params)
                
                # Validate response structure to catch potential issues early
                if not self._is_valid_response(response):
                    if attempt < MAX_RETRIES - 1:
                        delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                        logger.warning(f"⏳ Response invalide - tentative {attempt + 1}/{MAX_RETRIES}, attente {delay}s...")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error("❌ Réponse invalide après tous les retries")
                        return response
                
                # Validate JSON parsing for json_object responses
                # This catches cases where API returns malformed JSON, which may indicate
                # the API is under stress or experiencing issues
                if call_params.get('response_format', {}).get('type') == 'json_object':
                    if response.choices[0].message:
                        content = response.choices[0].message.content
                        if content:
                            try:
                                # Try to parse JSON to validate early
                                json.loads(content)
                            except json.JSONDecodeError:
                                # Retry on JSON parse errors as they may indicate API stress
                                if attempt < MAX_RETRIES - 1:
                                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                                    logger.warning(f"⏳ JSON malformé (API stress possible) - tentative {attempt + 1}/{MAX_RETRIES}, attente {delay}s...")
                                    time.sleep(delay)
                                    continue
                
                return response
                
            except SDKError as e:
                # Handle 429 rate limit errors explicitly
                if hasattr(e, 'status_code') and e.status_code == 429:
                    if attempt < MAX_RETRIES - 1:
                        delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                        logger.warning(f"⏳ Rate limit (429) - tentative {attempt + 1}/{MAX_RETRIES}, attente {delay}s...")
                        time.sleep(delay)
                        continue
                
                # Handle other potentially transient SDKErrors based on status codes
                # Common transient HTTP status codes: 408 (timeout), 503 (unavailable), 504 (gateway timeout)
                if hasattr(e, 'status_code') and e.status_code in [408, 500, 502, 503, 504]:
                    if attempt < MAX_RETRIES - 1:
                        delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                        logger.warning(f"⏳ Erreur transitoire HTTP {e.status_code} - tentative {attempt + 1}/{MAX_RETRIES}, attente {delay}s...")
                        time.sleep(delay)
                        continue
                
                # Fallback to string matching for errors without status codes (connection errors, etc.)
                error_message = str(e).lower()
                is_transient = any(keyword in error_message for keyword in TRANSIENT_ERROR_KEYWORDS)
                
                if is_transient and attempt < MAX_RETRIES - 1:
                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                    logger.warning(f"⏳ Erreur transitoire ({type(e).__name__}) - tentative {attempt + 1}/{MAX_RETRIES}, attente {delay}s...")
                    time.sleep(delay)
                    continue
                
                # Non-transient error or final attempt
                raise

    def _validate_and_parse_response(self, chat_response, expect_json: bool = True) -> dict:
        """
        Valide et parse une réponse Mistral API de manière sécurisée.
        
        Args:
            chat_response: Réponse brute de l'API
            expect_json: Si True, parse le contenu comme JSON
            
        Returns:
            dict: Contenu parsé ou dict vide en cas d'erreur
        """
        if not chat_response or not hasattr(chat_response, 'choices'):
            logger.error("❌ Réponse Mistral invalide : structure incorrecte")
            return {}
        
        if not chat_response.choices or len(chat_response.choices) == 0:
            logger.error("❌ Réponse Mistral invalide : pas de choices")
            return {}
        
        first_choice = chat_response.choices[0]
        if not hasattr(first_choice, 'message') or not first_choice.message:
            logger.error("❌ Réponse Mistral invalide : pas de message")
            return {}
        
        content = first_choice.message.content
        if not content:
            logger.error("❌ Réponse Mistral invalide : contenu vide")
            return {}
        
        if expect_json:
            try:
                result = json.loads(content)
                if not isinstance(result, dict):
                    logger.error("❌ Réponse JSON n'est pas un dictionnaire")
                    return {}
                return result
            except json.JSONDecodeError as e:
                logger.error(f"❌ Erreur parsing JSON : {e}")
                logger.error(f"Contenu reçu : {content[:200]}...")
                return {}
        else:
            return {"content": content}

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
            chat_response = self._chat_complete_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Titre : {title}\n\nContenu :\n{content}"}
                ],
                response_format={"type": "json_object"}
            )
            
            return self._validate_and_parse_response(chat_response, expect_json=True)
            
        except SDKError as e:
            logger.error(f"❌ Erreur SDK Mistral (après retries) : {e}")
            return {}
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'appel à l'API Mistral : {type(e).__name__}: {e}")
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
            chat_response = self._chat_complete_with_retry(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Texte source (Wikipedia) :\n\n{text}"}
                ],
                response_format={"type": "json_object"}
            )

            return self._validate_and_parse_response(chat_response, expect_json=True)
            
        except SDKError as e:
            logger.error(f"❌ Erreur SDK Mistral (après retries) : {e}")
            return {}
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'extraction de données : {type(e).__name__}: {e}")
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
            chat_response = self._chat_complete_with_retry(
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
        except SDKError as e:
            logger.error(f"❌ Erreur SDK Mistral (après retries) pour les entités RSS : {e}")
            return '[]'
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'appel Mistral pour les entités RSS : {e}")
            return '[]'
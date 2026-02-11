import os
import yaml
import logging

# Importation pour Mistral AI SDK v1.0+ (Version actuelle)
# Cela remplace l'ancien 'MistralClient' et l'import depuis 'models.models'
try:
    from mistralai import Mistral, ChatMessage
except ImportError:
    raise ImportError(
        "ERREUR : La bibliothèque 'mistralai' n'est pas installée ou est obsolète.\n"
        "Veuillez mettre à jour requirements.txt avec : mistralai>=1.0.0"
    )

logger = logging.getLogger("french_connection")

class MistralClient:
    """
    Client wrapper pour Mistral AI.
    Compatible avec Mistral SDK >= 1.0.0
    """
    
    def __init__(self):
        # 1. Sécurité : Vérification de la clé API
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError(
                "ERREUR CRITIQUE : MISTRAL_API_KEY n'est pas définie dans les variables d'environnement.\n"
                "Assurez-vous que le Secret est bien configuré dans GitHub Actions."
            )
        
        # 2. Initialisation du client (Syntaxe v1.0)
        try:
            self.client = Mistral(api_key=api_key)
        except Exception as e:
            raise ConnectionError(f"Impossible d'initialiser le client Mistral : {e}")

        # 3. Configuration du modèle
        self.model = os.getenv("MISTRAL_MODEL", "open-mistral-nemo")

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
        Analyse le texte, le classe, remplit le template et enrichit la structure.
        """
        template_content = self.load_template(template_path)
        
        system_prompt = f"""
        Tu es un expert en gestion de bases de connaissances pour le projet "French Connection".
        
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

        try:
            # Appel API Syntaxe v1.0+ : client.chat.complete(...)
            response = self.client.chat.complete(
                model=self.model,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_message)
                ],
                temperature=0.2
            )
            
            # Sécurité : Vérifier que la réponse contient des choix
            if not response.choices or not response.choices[0].message:
                logger.error(f"Réponse vide de l'IA pour {title}")
                return None

            yaml_str = response.choices[0].message.content
            
            # Nettoyage robuste de la réponse Markdown
            if "```yaml" in yaml_str:
                yaml_str = yaml_str.split("```yaml")[1].split("```")[0].strip()
            elif "```" in yaml_str:
                yaml_str = yaml_str.split("```")[1].split("```")[0].strip()

            # Validation du parsing YAML pour éviter de casser le fichier
            try:
                data = yaml.safe_load(yaml_str)
                if not isinstance(data, dict):
                    logger.error(f"L'IA n'a pas retourné un dictionnaire valide pour {title}")
                    return None
                return data 
            except yaml.YAMLError as e:
                logger.error(f"Erreur parsing YAML pour {title}: {e}\nExtrait: {yaml_str[:200]}")
                return None
                    
        except Exception as e:
            logger.error(f"Erreur critique IA pour {title} : {e}", exc_info=True)
            return None

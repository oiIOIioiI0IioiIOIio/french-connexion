import os
import yaml
import logging
# Import corrigé : on renomme l'import pour éviter le conflit avec notre classe
from mistralai.client import MistralClient as MistralAPIClient
# Import corrigé : le chemin a changé dans la nouvelle version de la librairie
from mistralai.models import ChatMessage

logger = logging.getLogger("french_connection")

class MistralClient:
    def __init__(self):
        # Chargement de la clé API
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY non définie. Vérifiez vos secrets GitHub.")
        
        # On utilise l'API Client renommé
        self.client = MistralAPIClient(api_key=api_key)
        self.model = "open-mistral-nemo"

    def load_template(self, template_path):
        """Charge le template YAML pour le fournir en contexte à l'IA."""
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"Template non trouvé : {template_path}")
            return "type: entity\n"

    def intelligent_restructure(self, text_content, title, template_path):
        """
        Analyse le texte, le classe, remplit le template ET propose des améliorations structurelles.
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
            response = self.client.chat(
                model=self.model,
                messages=[
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_message)
                ],
                temperature=0.2
            )
            
            yaml_str = response.choices[0].message.content
            
            # Nettoyage de la réponse
            if "```yaml" in yaml_str:
                yaml_str = yaml_str.split("```yaml")[1].split("```")[0].strip()
            elif "```" in yaml_str:
                yaml_str = yaml_str.split("```")[1].split("```")[0].strip()

            # Validation parsing
            data = yaml.safe_load(yaml_str)
            return data 
            
        except Exception as e:
            logger.error(f"Erreur lors de la restructuration IA pour {title} : {e}")
            return None

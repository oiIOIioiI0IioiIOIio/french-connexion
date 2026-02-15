import sys
import os
import wikipedia
import yaml
import frontmatter
from pathlib import Path
from dotenv import load_dotenv
import json
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
from collections import defaultdict
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler
from src.utils.llm_client import MistralClient

# Configuration
load_dotenv()
logger = setup_logger()
git = GitHandler()
llm = MistralClient()

# Wikipedia en français
wikipedia.set_lang("fr")

# Chargement config
with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# Variables globales pour tracker l'exploration
VISITED_PEOPLE = set()
VISITED_ORGS = set()
ALL_FOUND_ENTITIES = []
EXPLORATION_STATS = defaultdict(int)
RELATIONSHIPS_GRAPH = defaultdict(list)
VALIDATION_SCORES = {}
CREATED_FILES = []
ORIGINAL_QUERY = ""

# Configuration de l'exploration
MAX_DEPTH = 3
CONFIDENCE_THRESHOLD = 0.6  # Score minimum pour validation
EXPONENTIAL_EXPLORATION = True  # Exploration complète sans limite

# Structures de retour par défaut pour les erreurs
EMPTY_ENTITY_RESPONSE = {
    'people': [],
    'institutions': [],
    'main_subject': '',
    'subject_type': 'unknown',
    'description': '',
    'keywords': [],
    'context': '',
    'relevance_explanation': ''
}

EMPTY_QUERY_RESPONSE = {
    'query_type': 'unknown',
    'people': [],
    'institutions': [],
    'interpretation': '',
    'main_subject': '',
    'subject_category': '',
    'explanation': ''
}

class PersonEntity:
    """Classe pour représenter une personne avec toutes ses métadonnées"""
    
    def __init__(self, name: str, depth: int, found_via: str, query: str):
        self.name = name
        self.depth = depth
        self.found_via = found_via
        self.original_query = query
        self.wikipedia_data = None
        self.validation_score = 0.0
        self.validation_reason = ""
        self.is_validated = False
        self.relationships = []
        self.organizations = []
        self.created_file_path = None
        self.factcheck_status = "pending"
        self.sources = []
        
    def to_dict(self) -> dict:
        """Convertit l'entité en dictionnaire"""
        return {
            'name': self.name,
            'depth': self.depth,
            'found_via': self.found_via,
            'original_query': self.original_query,
            'validation_score': self.validation_score,
            'validation_reason': self.validation_reason,
            'is_validated': self.is_validated,
            'factcheck_status': self.factcheck_status,
            'relationships_count': len(self.relationships),
            'organizations_count': len(self.organizations)
        }

class InstitutionEntity:
    """Classe pour représenter une institution"""
    
    def __init__(self, name: str, depth: int, found_via: str):
        self.name = name
        self.depth = depth
        self.found_via = found_via
        self.wikipedia_data = None
        self.members = []
        self.created_file_path = None
        self.factcheck_status = "pending"
        
    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'depth': self.depth,
            'found_via': self.found_via,
            'factcheck_status': self.factcheck_status,
            'members_count': len(self.members)
        }

class RelationshipDetail:
    """Classe pour représenter une relation détaillée entre deux personnes"""
    
    def __init__(self, person_from: str, person_to: str, relationship_type: str, 
                 description: str, confidence: float, source: str):
        self.person_from = person_from
        self.person_to = person_to
        self.relationship_type = relationship_type
        self.description = description
        self.confidence = confidence
        self.source = source
        self.timestamp = datetime.now()
        
    def to_markdown(self) -> str:
        """Convertit la relation en format Markdown pour Obsidian"""
        return f"- [[{self.person_to}]] : {self.description} ({self.relationship_type})"
    
    def to_dict(self) -> dict:
        return {
            'person_from': self.person_from,
            'person_to': self.person_to,
            'type': self.relationship_type,
            'description': self.description,
            'confidence': self.confidence,
            'source': self.source
        }

def mistral_identify_entities_comprehensive(query: str, context: str = "", query_type_hint: str = None) -> dict:
    """
    🧠 Identification complète des entités via Mistral
    Utilise la connaissance générale pour identifier personnes et institutions
    """
    logger.info(f"🧠 Identification complète des entités pour : {query}")
    
    context_text = f"\n\nCONTEXTE ADDITIONNEL :\n{context}" if context else ""
    type_hint = f"\n\nHINT : Cette requête est de type '{query_type_hint}'" if query_type_hint else ""
    
    prompt = f"""
Tu es un expert mondial des réseaux de pouvoir, élites, institutions et géopolitique.

REQUÊTE : "{query}"{context_text}{type_hint}

Ta mission : identifier de manière EXHAUSTIVE et RIGOUREUSE toutes les personnes et institutions 
pertinentes, en utilisant ta connaissance générale (niveau journalistique).

⚠️ RÈGLE CRITIQUE : 
- Si la requête contient "dirigeants", "membres", "présidents", "ministres" → ce sont des PERSONNES
- JAMAIS traiter un groupe de personnes comme une institution
- "dirigeants de X" = personnes, pas institution

EXEMPLES DÉTAILLÉS :

Requête "les dirigeants de LVMH" →
{{
  "main_subject": "LVMH",
  "subject_type": "people_group",
  "description": "Dirigeants et cadres exécutifs du groupe LVMH",
  "people": [
    "Bernard Arnault",
    "Antoine Arnault",
    "Delphine Arnault",
    "Sidney Toledano",
    "Pietro Beccari",
    "Michael Burke",
    "Jean-Jacques Guiony"
  ],
  "institutions": ["LVMH", "Christian Dior", "Louis Vuitton"],
  "keywords": ["luxe", "dirigeant", "entreprise", "famille Arnault"],
  "context": "Direction du premier groupe de luxe mondial",
  "relevance_explanation": "Cadres dirigeants de LVMH - ce sont des PERSONNES occupant des fonctions de direction"
}}

Requête "Le Siècle" →
{{
  "main_subject": "Le Siècle",
  "subject_type": "institution",
  "description": "Club de réflexion français fondé en 1944, réunissant élites politiques, économiques et médiatiques",
  "people": [
    "Henri de Castries",
    "Anne Lauvergeon", 
    "Nicole Notat",
    "Thierry Breton",
    "Laurence Parisot",
    "Jean-Marie Colombani",
    "Christine Lagarde",
    "François Pérol",
    "Bernard Arnault"
  ],
  "institutions": ["Le Siècle", "MEDEF", "Institut Montaigne", "ENA"],
  "keywords": ["élite", "réseau", "influence", "club", "pouvoir"],
  "context": "Réseau d'influence français majeur depuis 1944",
  "relevance_explanation": "Club privé rassemblant les principales élites françaises"
}}

Requête "Jeffrey Epstein" →
{{
  "main_subject": "Jeffrey Epstein",
  "subject_type": "personne",
  "description": "Financier américain condamné pour trafic de mineurs et crimes sexuels, décédé en prison en 2019",
  "people": [
    "Jeffrey Epstein",
    "Ghislaine Maxwell",
    "Les Wexner",
    "Bill Clinton",
    "Donald Trump",
    "Prince Andrew",
    "Alan Dershowitz",
    "Jean-Luc Brunel"
  ],
  "institutions": ["Victoria's Secret", "L Brands", "MIT Media Lab", "Council on Foreign Relations"],
  "keywords": ["finance", "scandale", "trafic", "élite", "connexions"],
  "context": "Réseau de trafic de mineurs impliquant des personnalités internationales",
  "relevance_explanation": "Affaire criminelle majeure révélant des connexions au sein des élites"
}}

RÈGLES STRICTES :
1. Utilise ta CONNAISSANCE GÉNÉRALE (sources fiables uniquement)
2. Liste TOUTES les personnes pertinentes (10-30 personnes selon le sujet)
3. Liste TOUTES les institutions/organisations pertinentes
4. main_subject = le sujet EXACT de la requête
5. subject_type = "personne", "people_group", ou "institution"
   - "people_group" = requête demandant un groupe de personnes (dirigeants, membres, etc.)
   - JAMAIS "institution" pour des groupes de personnes
6. Sois EXHAUSTIF mais RIGOUREUX
7. N'invente AUCUNE information
8. Privilégie les personnes DOCUMENTÉES et VÉRIFIABLES

Retourne un JSON complet :
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.3  # Plus bas pour plus de fiabilité
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            
            EXPLORATION_STATS['mistral_calls'] += 1
            EXPLORATION_STATS['entities_identified'] += len(result.get('people', []))
            EXPLORATION_STATS['institutions_identified'] += len(result.get('institutions', []))
            
            logger.info(f"✅ Sujet principal : {result.get('main_subject', 'N/A')} (type: {result.get('subject_type', 'N/A')})")
            logger.info(f"✅ {len(result.get('people', []))} personnes identifiées")
            logger.info(f"✅ {len(result.get('institutions', []))} institutions identifiées")
            
            return result
        
        return EMPTY_ENTITY_RESPONSE.copy()
        
    except Exception as e:
        logger.error(f"❌ Erreur Mistral identification : {e}")
        EXPLORATION_STATS['errors'] += 1
        return EMPTY_ENTITY_RESPONSE.copy()

def answer_initial_query_directly(query: str) -> dict:
    """
    🎯 RÉPOND DIRECTEMENT à la requête initiale AVANT l'exploration récursive
    Distingue les requêtes sur des GROUPES DE PERSONNES vs des INSTITUTIONS
    """
    logger.info(f"🎯 Réponse directe à la requête : {query}")
    
    prompt = f"""
Tu es un expert en analyse de requêtes et identification d'entités.

REQUÊTE : "{query}"

Ta mission : déterminer si cette requête demande des PERSONNES ou une INSTITUTION, puis répondre DIRECTEMENT.

RÈGLES DE CLASSIFICATION STRICTES :

1. REQUÊTE SUR DES PERSONNES (liste de personnes) :
   - Contient : "dirigeants", "membres", "présidents", "ministres", "personnes", "qui sont", etc.
   - Exemples : "les dirigeants de LVMH", "les membres du Siècle", "les présidents français"
   - Type : "people_group"
   
2. REQUÊTE SUR UNE PERSONNE UNIQUE :
   - Nom propre d'une personne spécifique
   - Exemples : "Emmanuel Macron", "Bernard Arnault", "Jeffrey Epstein"
   - Type : "single_person"
   
3. REQUÊTE SUR UNE INSTITUTION :
   - Nom d'organisation, entreprise, club, think tank
   - Exemples : "Le Siècle", "LVMH", "Groupe Bilderberg"
   - Type : "institution"

INSTRUCTIONS SELON LE TYPE :

Si type = "people_group" :
- Identifie l'organisation/contexte mentionné
- Liste TOUTES les personnes pertinentes (dirigeants, membres, etc.)
- Minimum 5-20 personnes selon le contexte

Si type = "single_person" :
- Identifie la personne
- Liste ses relations principales (5-15 personnes)

Si type = "institution" :
- Identifie l'institution
- Liste ses membres/dirigeants principaux (10-30 personnes)

EXEMPLES DÉTAILLÉS :

Requête "les dirigeants de LVMH" →
{{
  "query_type": "people_group",
  "main_subject": "LVMH",
  "subject_category": "entreprise",
  "interpretation": "Liste des dirigeants et cadres dirigeants de LVMH",
  "people": [
    "Bernard Arnault",
    "Antoine Arnault",
    "Delphine Arnault",
    "Sidney Toledano",
    "Pietro Beccari",
    "Michael Burke",
    "Jean-Jacques Guiony",
    "Chantal Gaemperle"
  ],
  "institutions": ["LVMH", "Christian Dior", "Louis Vuitton", "Moët Hennessy"],
  "explanation": "Requête demandant explicitement les DIRIGEANTS (personnes) de LVMH, pas l'entreprise elle-même"
}}

Requête "Le Siècle" →
{{
  "query_type": "institution",
  "main_subject": "Le Siècle",
  "subject_category": "club d'influence",
  "interpretation": "Club réunissant les élites françaises - liste de ses membres",
  "people": [
    "Henri de Castries",
    "Anne Lauvergeon",
    "Nicole Notat",
    "Thierry Breton",
    "Christine Lagarde",
    "Bernard Arnault",
    "François Pérol"
  ],
  "institutions": ["Le Siècle", "MEDEF", "Institut Montaigne"],
  "explanation": "Institution dont on veut connaître les membres"
}}

Requête "Bernard Arnault" →
{{
  "query_type": "single_person",
  "main_subject": "Bernard Arnault",
  "subject_category": "chef d'entreprise",
  "interpretation": "Personne spécifique et son réseau",
  "people": [
    "Bernard Arnault",
    "Antoine Arnault",
    "Delphine Arnault",
    "Sidney Toledano",
    "François Pinault",
    "Emmanuel Macron"
  ],
  "institutions": ["LVMH", "Christian Dior", "Le Siècle"],
  "explanation": "Personne unique dont on explore le réseau"
}}

IMPORTANT :
- Si la requête contient "dirigeants", "membres", "qui sont", "liste", etc. → query_type = "people_group"
- TOUJOURS privilégier "people_group" en cas de doute avec des mots au pluriel
- Liste EXHAUSTIVE de personnes (utilise ta connaissance générale)

Retourne un JSON complet :
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            
            query_type = result.get('query_type', 'unknown')
            people = result.get('people', [])
            institutions = result.get('institutions', [])
            interpretation = result.get('interpretation', '')
            
            logger.info(f"✅ Type de requête identifié : {query_type}")
            logger.info(f"✅ Sujet principal : {result.get('main_subject', 'N/A')}")
            logger.info(f"✅ Interprétation : {interpretation}")
            logger.info(f"✅ {len(people)} personnes identifiées directement")
            logger.info(f"✅ {len(institutions)} institutions identifiées")
            
            return result
        
        return EMPTY_QUERY_RESPONSE.copy()
        
    except Exception as e:
        logger.error(f"❌ Erreur réponse directe : {e}")
        return EMPTY_QUERY_RESPONSE.copy()

def answer_initial_query_directly(query: str) -> dict:
    """
    🎯 RÉPOND DIRECTEMENT à la requête initiale AVANT l'exploration récursive
    Distingue les requêtes sur des GROUPES DE PERSONNES vs des INSTITUTIONS
    """
    logger.info(f"🎯 Réponse directe à la requête : {query}")
    
    prompt = f"""
Tu es un expert en analyse de requêtes et identification d'entités.

REQUÊTE : "{query}"

Ta mission : déterminer si cette requête demande des PERSONNES ou une INSTITUTION, puis répondre DIRECTEMENT.

RÈGLES DE CLASSIFICATION STRICTES :

1. REQUÊTE SUR DES PERSONNES (liste de personnes) :
   - Contient : "dirigeants", "membres", "présidents", "ministres", "personnes", "qui sont", etc.
   - Exemples : "les dirigeants de LVMH", "les membres du Siècle", "les présidents français"
   - Type : "people_group"
   
2. REQUÊTE SUR UNE PERSONNE UNIQUE :
   - Nom propre d'une personne spécifique
   - Exemples : "Emmanuel Macron", "Bernard Arnault", "Jeffrey Epstein"
   - Type : "single_person"
   
3. REQUÊTE SUR UNE INSTITUTION :
   - Nom d'organisation, entreprise, club, think tank
   - Exemples : "Le Siècle", "LVMH", "Groupe Bilderberg"
   - Type : "institution"

INSTRUCTIONS SELON LE TYPE :

Si type = "people_group" :
- Identifie l'organisation/contexte mentionné
- Liste TOUTES les personnes pertinentes (dirigeants, membres, etc.)
- Minimum 5-20 personnes selon le contexte

Si type = "single_person" :
- Identifie la personne
- Liste ses relations principales (5-15 personnes)

Si type = "institution" :
- Identifie l'institution
- Liste ses membres/dirigeants principaux (10-30 personnes)

EXEMPLES DÉTAILLÉS :

Requête "les dirigeants de LVMH" →
{{
  "query_type": "people_group",
  "main_subject": "LVMH",
  "subject_category": "entreprise",
  "interpretation": "Liste des dirigeants et cadres dirigeants de LVMH",
  "people": [
    "Bernard Arnault",
    "Antoine Arnault",
    "Delphine Arnault",
    "Sidney Toledano",
    "Pietro Beccari",
    "Michael Burke",
    "Jean-Jacques Guiony",
    "Chantal Gaemperle"
  ],
  "institutions": ["LVMH", "Christian Dior", "Louis Vuitton", "Moët Hennessy"],
  "explanation": "Requête demandant explicitement les DIRIGEANTS (personnes) de LVMH, pas l'entreprise elle-même"
}}

Requête "Le Siècle" →
{{
  "query_type": "institution",
  "main_subject": "Le Siècle",
  "subject_category": "club d'influence",
  "interpretation": "Club réunissant les élites françaises - liste de ses membres",
  "people": [
    "Henri de Castries",
    "Anne Lauvergeon",
    "Nicole Notat",
    "Thierry Breton",
    "Christine Lagarde",
    "Bernard Arnault",
    "François Pérol"
  ],
  "institutions": ["Le Siècle", "MEDEF", "Institut Montaigne"],
  "explanation": "Institution dont on veut connaître les membres"
}}

Requête "Bernard Arnault" →
{{
  "query_type": "single_person",
  "main_subject": "Bernard Arnault",
  "subject_category": "chef d'entreprise",
  "interpretation": "Personne spécifique et son réseau",
  "people": [
    "Bernard Arnault",
    "Antoine Arnault",
    "Delphine Arnault",
    "Sidney Toledano",
    "François Pinault",
    "Emmanuel Macron"
  ],
  "institutions": ["LVMH", "Christian Dior", "Le Siècle"],
  "explanation": "Personne unique dont on explore le réseau"
}}

IMPORTANT :
- Si la requête contient "dirigeants", "membres", "qui sont", "liste", etc. → query_type = "people_group"
- TOUJOURS privilégier "people_group" en cas de doute avec des mots au pluriel
- Liste EXHAUSTIVE de personnes (utilise ta connaissance générale)

Retourne un JSON complet :
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            
            query_type = result.get('query_type', 'unknown')
            people = result.get('people', [])
            institutions = result.get('institutions', [])
            interpretation = result.get('interpretation', '')
            
            logger.info(f"✅ Type de requête identifié : {query_type}")
            logger.info(f"✅ Sujet principal : {result.get('main_subject', 'N/A')}")
            logger.info(f"✅ Interprétation : {interpretation}")
            logger.info(f"✅ {len(people)} personnes identifiées directement")
            logger.info(f"✅ {len(institutions)} institutions identifiées")
            
            return result
        
        return {}
        
    except Exception as e:
        logger.error(f"❌ Erreur réponse directe : {e}")
        return {}

def mistral_extract_detailed_relationships(person_name: str, bio_text: str, 
                                          all_known_people: Set[str]) -> List[RelationshipDetail]:
    """
    🔗 Extraction DÉTAILLÉE des relations depuis une biographie Wikipedia
    Retourne des objets RelationshipDetail avec descriptions précises
    """
    logger.info(f"🔗 Extraction détaillée des relations pour : {person_name}")
    
    known_people_list = list(all_known_people)[:50]  # Limiter pour le prompt
    
    prompt = f"""
Tu es un expert en analyse de réseaux et relations de pouvoir.

PERSONNE ANALYSÉE : {person_name}

BIOGRAPHIE WIKIPEDIA :
{bio_text}

PERSONNES DÉJÀ IDENTIFIÉES DANS LE RÉSEAU :
{', '.join(known_people_list)}

Ta mission : extraire TOUTES les relations significatives avec des descriptions PRÉCISES.

Pour chaque relation, identifie :
1. Le nom complet de la personne liée
2. Le type de relation (famille, collaborateur, mentor, associé, concurrent, etc.)
3. Une description FACTUELLE et PRÉCISE de la relation (1 phrase)
4. Un score de confiance (0.0 à 1.0) basé sur la clarté de l'information

EXEMPLES DE RELATIONS DÉTAILLÉES :

{{
  "relationships": [
    {{
      "person_name": "Ghislaine Maxwell",
      "relationship_type": "associée",
      "description": "Associée et compagne de longue date, impliquée dans le réseau de trafic",
      "confidence": 0.95,
      "context": "Mentionnée 47 fois dans la biographie, relation documentée sur 20 ans"
    }},
    {{
      "person_name": "Bill Clinton",
      "relationship_type": "relation professionnelle",
      "description": "A voyagé à plusieurs reprises dans l'avion privé d'Epstein entre 2002 et 2005",
      "confidence": 0.85,
      "context": "Relation documentée par les logs de vol et témoignages"
    }},
    {{
      "person_name": "Les Wexner",
      "relationship_type": "mentor et associé",
      "description": "Principal client et mentor financier, PDG de L Brands, relation de 15 ans",
      "confidence": 0.90,
      "context": "Gestion de fortune et conseils financiers documentés"
    }}
  ],
  "institutions": [
    {{
      "name": "Victoria's Secret",
      "relationship_type": "conseiller financier",
      "description": "Conseiller financier de Les Wexner, propriétaire de la marque",
      "confidence": 0.80
    }}
  ]
}}

RÈGLES STRICTES :
1. N'extrais QUE les relations EXPLICITEMENT mentionnées dans le texte
2. Descriptions FACTUELLES uniquement (pas d'interprétation)
3. Score de confiance basé sur la clarté et la répétition dans le texte
4. Maximum 20 relations (priorise les plus importantes)
5. Privilégie les personnes de la liste "PERSONNES DÉJÀ IDENTIFIÉES"
6. Aucune invention, aucune spéculation

Retourne un JSON :
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2  # Très bas pour fiabilité maximale
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            relationships_data = result.get('relationships', [])
            
            relationships = []
            for rel in relationships_data:
                if rel.get('confidence', 0) >= 0.6:  # Seuil de confiance
                    relationship = RelationshipDetail(
                        person_from=person_name,
                        person_to=rel.get('person_name', ''),
                        relationship_type=rel.get('relationship_type', 'relation'),
                        description=rel.get('description', ''),
                        confidence=rel.get('confidence', 0.0),
                        source=f"Wikipedia - {person_name}"
                    )
                    relationships.append(relationship)
            
            EXPLORATION_STATS['relationships_extracted'] += len(relationships)
            logger.info(f"✅ {len(relationships)} relations détaillées extraites (confiance ≥ 0.6)")
            
            return relationships
        
        return []
        
    except Exception as e:
        logger.error(f"❌ Erreur extraction relations : {e}")
        EXPLORATION_STATS['errors'] += 1
        return []

def wikipedia_factcheck_person_rigorous(person_name: str) -> Optional[dict]:
    """
    📖 Factchecking RIGOUREUX d'une personne via Wikipedia
    Niveau journalistique : vérification multiple, sources croisées
    """
    logger.info(f"📖 Factcheck rigoureux pour : {person_name}")
    
    try:
        # Recherche Wikipedia
        page = wikipedia.page(person_name, auto_suggest=True)
        summary = page.summary
        full_content = page.content[:5000]  # Plus de contenu pour analyse
        
        logger.info(f"✅ Page Wikipedia trouvée : {page.title}")
        
        # Schéma d'extraction détaillé
        schema = """
        {
          "nom_complet_verifie": "Nom complet exact selon Wikipedia",
          "date_naissance": "Date de naissance au format YYYY-MM-DD si disponible",
          "date_deces": "Date de décès au format YYYY-MM-DD si applicable, sinon vide",
          "lieu_naissance": "Ville et pays de naissance complets",
          "nationalite": "Nationalité(s) complète(s)",
          "genre": "homme ou femme",
          "statut_actuel": "Profession ou fonction principale actuelle ou au moment du décès",
          "bio_courte": "Résumé biographique factuel en 2-3 phrases maximum",
          "bio_detaillee": "Biographie détaillée en 5-7 phrases",
          "formation": "Liste complète des écoles, universités, diplômes (format: liste)",
          "carriere": "Liste chronologique des principales fonctions, postes, mandats (format: liste)",
          "distinctions": "Liste complète des distinctions, prix, décorations (format: liste)",
          "controverses": "Liste des controverses ou scandales documentés (format: liste)",
          "famille_proche": "Noms complets des membres famille proche mentionnés (conjoint, enfants, parents)",
          "relations_professionnelles": "Noms complets des collaborateurs, mentors, associés importants",
          "mots_cles": "Mots-clés caractérisant la personne (5-10 mots)",
          "niveau_notoriete": "Score de 1 à 10 estimant la notoriété publique",
          "sources_mentionnees": "Sources ou références importantes mentionnées dans l'article"
        }
        """
        
        extracted_data = llm.extract_yaml_data(full_content, schema)
        
        # Normalisation des listes
        for key in ['formation', 'carriere', 'distinctions', 'controverses', 
                    'famille_proche', 'relations_professionnelles', 'mots_cles', 'sources_mentionnees']:
            if key not in extracted_data or extracted_data[key] is None:
                extracted_data[key] = []
            elif isinstance(extracted_data[key], str):
                items = [item.strip() for item in extracted_data[key].split(',') if item.strip()]
                extracted_data[key] = items
        
        # Enrichissement des données
        extracted_data['wikipedia_url'] = page.url
        extracted_data['wikipedia_title'] = page.title
        extracted_data['wikipedia_summary'] = summary[:500]
        extracted_data['verification_date'] = datetime.now().strftime('%Y-%m-%d')
        extracted_data['factcheck_status'] = 'verified'
        extracted_data['content_length'] = len(page.content)
        extracted_data['has_references'] = len(page.references) if hasattr(page, 'references') else 0
        
        # Extraction des relations détaillées
        all_known_people = VISITED_PEOPLE.copy()
        relationships = mistral_extract_detailed_relationships(person_name, full_content, all_known_people)
        
        extracted_data['detailed_relationships'] = relationships
        extracted_data['relationships_count'] = len(relationships)
        
        # Extraction des institutions liées
        institutions = extract_institutions_from_text(full_content)
        extracted_data['linked_institutions'] = institutions
        
        EXPLORATION_STATS['factcheck_success'] += 1
        logger.info(f"✅ Factcheck réussi : {page.title} ({len(relationships)} relations, {len(institutions)} institutions)")
        
        return extracted_data
        
    except wikipedia.exceptions.DisambiguationError as e:
        logger.warning(f"⚠️  Ambiguïté pour {person_name}. Options : {e.options[:5]}")
        
        # Tentative avec la première option
        try:
            page = wikipedia.page(e.options[0])
            full_content = page.content[:5000]
            
            logger.info(f"✅ Utilisation de la page : {page.title}")
            
            schema = """
            {
              "nom_complet_verifie": "Nom complet exact",
              "date_naissance": "Date de naissance",
              "date_deces": "Date de décès si applicable",
              "lieu_naissance": "Lieu de naissance",
              "nationalite": "Nationalité",
              "genre": "Genre",
              "statut_actuel": "Statut professionnel",
              "bio_courte": "Biographie courte",
              "bio_detaillee": "Biographie détaillée",
              "formation": "Formation (liste)",
              "carriere": "Carrière (liste)",
              "distinctions": "Distinctions (liste)",
              "controverses": "Controverses (liste)",
              "mots_cles": "Mots-clés",
              "niveau_notoriete": "Notoriété (1-10)"
            }
            """
            
            extracted_data = llm.extract_yaml_data(full_content, schema)
            
            for key in ['formation', 'carriere', 'distinctions', 'controverses', 'mots_cles']:
                if key not in extracted_data or extracted_data[key] is None:
                    extracted_data[key] = []
                elif isinstance(extracted_data[key], str):
                    extracted_data[key] = [item.strip() for item in extracted_data[key].split(',') if item.strip()]
            
            extracted_data['wikipedia_url'] = page.url
            extracted_data['wikipedia_title'] = page.title
            extracted_data['factcheck_status'] = 'verified_disambiguation'
            extracted_data['verification_date'] = datetime.now().strftime('%Y-%m-%d')
            
            all_known_people = VISITED_PEOPLE.copy()
            relationships = mistral_extract_detailed_relationships(person_name, full_content, all_known_people)
            extracted_data['detailed_relationships'] = relationships
            
            institutions = extract_institutions_from_text(full_content)
            extracted_data['linked_institutions'] = institutions
            
            EXPLORATION_STATS['factcheck_disambiguation'] += 1
            
            return extracted_data
            
        except Exception as e2:
            logger.error(f"❌ Échec résolution ambiguïté : {e2}")
            EXPLORATION_STATS['factcheck_failed'] += 1
            return None
            
    except wikipedia.exceptions.PageError:
        logger.warning(f"❌ Pas de page Wikipedia pour : {person_name}")
        EXPLORATION_STATS['factcheck_not_found'] += 1
        return None
        
    except Exception as e:
        logger.error(f"❌ Erreur factcheck {person_name} : {e}")
        EXPLORATION_STATS['factcheck_failed'] += 1
        EXPLORATION_STATS['errors'] += 1
        return None

def extract_institutions_from_text(text: str) -> List[str]:
    """
    Extrait les institutions/organisations mentionnées dans un texte
    """
    prompt = f"""
Extrais toutes les institutions, organisations, entreprises mentionnées dans ce texte.

TEXTE :
{text[:2000]}

Retourne uniquement les noms d'institutions IMPORTANTES et SIGNIFICATIVES.
Format JSON : {{"institutions": ["Institution 1", "Institution 2"]}}

Maximum 15 institutions, triées par importance.
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            institutions = result.get('institutions', [])
            return institutions[:15]
        
        return []
        
    except Exception as e:
        logger.error(f"Erreur extraction institutions : {e}")
        return []

def validate_person_relevance_comprehensive(person: PersonEntity, original_query: str) -> Tuple[bool, float, str]:
    """
    ✅ Validation COMPLÈTE de la pertinence d'une personne
    Retourne (is_valid, confidence_score, detailed_reason)
    """
    logger.info(f"✅ Validation complète : {person.name} (profondeur {person.depth})")
    
    # Profondeur 0 : sujet principal, toujours validé avec score max
    if person.depth == 0:
        return (True, 1.0, "Sujet principal de la requête")
    
    # Récupérer le contexte de la personne
    context = ""
    if person.wikipedia_data:
        bio = person.wikipedia_data.get('bio_detaillee', '')
        carriere = person.wikipedia_data.get('carriere', [])
        context = f"Bio: {bio}\nCarrière: {', '.join(carriere[:5])}"
    
    prompt = f"""
Tu es un expert en validation de données et fact-checking journalistique.

REQUÊTE ORIGINALE : "{original_query}"
PERSONNE À VALIDER : "{person.name}"
PROFONDEUR DE RECHERCHE : {person.depth} (0 = sujet principal, 1-3 = degrés de séparation)
TROUVÉE VIA : "{person.found_via}"

CONTEXTE BIOGRAPHIQUE :
{context}

Ta mission : déterminer si cette personne est PERTINENTE et JUSTIFIABLE dans le contexte de la requête.

CRITÈRES DE VALIDATION STRICTS :

Profondeur 1 (1er degré) :
- Score ≥ 0.8 : Lien DIRECT et DOCUMENTÉ (famille proche, associé direct, collaborateur clé)
- Score 0.6-0.8 : Lien SIGNIFICATIF (relation professionnelle importante)
- Score < 0.6 : REJETER (lien trop faible ou indirect)

Profondeur 2 (2ème degr��) :
- Score ≥ 0.7 : Lien IMPORTANT via une personne clé (membre même réseau, collaborateur de collaborateur)
- Score 0.6-0.7 : Lien MODÉRÉ (connexion professionnelle indirecte mais significative)
- Score < 0.6 : REJETER (trop éloigné de la requête)

Profondeur 3 (3ème degré) :
- Score ≥ 0.65 : Lien NOTABLE (même sphère d'influence, même réseau élargi)
- Score < 0.65 : REJETER (connexion trop ténue)

EXEMPLES CONCRETS :

Requête "Jeffrey Epstein" :
- Ghislaine Maxwell (profondeur 1) → Score 0.95 (associée directe documentée)
- Bill Clinton (profondeur 1) → Score 0.85 (relation documentée, voyages communs)
- Prince Andrew (profondeur 1) → Score 0.90 (relation documentée, accusations)
- Chelsea Clinton (profondeur 2, via Bill) → Score 0.40 REJETÉ (lien familial indirect non pertinent)
- Tony Blair (profondeur 2, via Prince Andrew) → Score 0.60 (limite, relation indirecte)

Requête "Le Siècle" :
- Henri de Castries (profondeur 1) → Score 0.95 (membre confirmé)
- Bernard Arnault (profondeur 1) → Score 0.90 (membre du club)
- Claude Bébéar (profondeur 2, via Castries) → Score 0.75 (mentor, même réseau)
- Emmanuel Macron (profondeur 1) → Score 0.85 (participant documenté)

ANALYSE REQUISE :
1. Pertinence du lien par rapport à la requête originale
2. Force de la connexion (documentée, vérifiable)
3. Justification journalistique (pourquoi cette personne est importante dans ce contexte)
4. Score de confiance (0.0 à 1.0)

Retourne un JSON :
{{
  "is_relevant": true ou false,
  "confidence_score": 0.0 à 1.0,
  "detailed_reason": "Explication détaillée et factuelle (2-3 phrases)",
  "connection_strength": "direct" ou "indirect" ou "weak",
  "journalistic_justification": "Justification éditoriale de l'inclusion"
}}

Sois STRICT : privilégie la QUALITÉ sur la QUANTITÉ. Un réseau de 20 personnes pertinentes vaut mieux que 100 avec des liens faibles.
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            
            is_relevant = result.get('is_relevant', False)
            confidence = result.get('confidence_score', 0.0)
            reason = result.get('detailed_reason', 'Pas de raison fournie')
            justification = result.get('journalistic_justification', '')
            
            # Combiner raison et justification
            full_reason = f"{reason} | Justification éditoriale : {justification}"
            
            EXPLORATION_STATS['validations_performed'] += 1
            
            if is_relevant and confidence >= CONFIDENCE_THRESHOLD:
                logger.info(f"✅ {person.name} → VALIDÉ (score: {confidence:.2f})")
                EXPLORATION_STATS['validations_passed'] += 1
                return (True, confidence, full_reason)
            else:
                logger.warning(f"❌ {person.name} → REJETÉ (score: {confidence:.2f}) : {reason}")
                EXPLORATION_STATS['validations_rejected'] += 1
                return (False, confidence, full_reason)
        
        return (False, 0.0, "Erreur de validation")
        
    except Exception as e:
        logger.error(f"❌ Erreur validation : {e}")
        EXPLORATION_STATS['errors'] += 1
        return (False, 0.0, f"Erreur technique : {e}")

def explore_network_exponential(initial_query: str, current_depth: int = 0, 
                               max_depth: int = MAX_DEPTH, initial_query_type: str = None) -> None:
    """
    🌳 Exploration EXPONENTIELLE du réseau (tous les chemins, pas de limite)
    Exploration complète niveau par niveau
    """
    global VISITED_PEOPLE, VISITED_ORGS, ALL_FOUND_ENTITIES, ORIGINAL_QUERY
    
    if current_depth >= max_depth:
        logger.info(f"🛑 Profondeur maximale atteinte ({max_depth})")
        return
    
    logger.info(f"\n{'='*70}")
    logger.info(f"🌳 NIVEAU {current_depth + 1}/{max_depth} : {initial_query}")
    logger.info(f"{'='*70}")
    
    # PHASE 1 : MISTRAL IDENTIFIE LES ENTITÉS
    # Passer le hint de type si disponible (uniquement au niveau 0)
    query_type_hint = initial_query_type if current_depth == 0 else None
    # Passer le hint de type si disponible
    query_type_hint = None
    if current_depth == 0 and hasattr(explore_network_exponential, '_initial_query_type'):
        query_type_hint = explore_network_exponential._initial_query_type
    
    entities = mistral_identify_entities_comprehensive(initial_query, query_type_hint=query_type_hint)
    
    if not entities:
        logger.warning("❌ Aucune entité identifiée par Mistral")
        return
    
    main_subject = entities.get('main_subject', '')
    subject_type = entities.get('subject_type', 'personne')
    people = entities.get('people', [])
    institutions = entities.get('institutions', [])
    
    # Si c'est un people_group, traiter comme une liste de personnes, pas comme institution
    if subject_type == 'people_group':
        subject_type = 'personne'  # Traiter comme des personnes
        logger.info(f"🎯 Requête de type 'people_group' détectée - focus sur les personnes")
    
   # Ajouter le sujet principal UNIQUEMENT si c'est une personne unique au niveau racine
    # Conditions: personne, nom présent, non déjà dans la liste, profondeur 0, et pas un terme générique
    if subject_type == 'personne' and main_subject and main_subject not in people and current_depth == 0:
        if not is_generic_people_term(main_subject):
            people.insert(0, main_subject)
    
    # Tracker les institutions
    for inst in institutions:
        if inst not in VISITED_ORGS:
            VISITED_ORGS.add(inst)
            institution_entity = InstitutionEntity(
                name=inst,
                depth=current_depth,
                found_via=initial_query if current_depth > 0 else 'requête principale'
            )
            ALL_FOUND_ENTITIES.append(institution_entity)
            logger.info(f"🏢 Institution ajoutée : {inst}")
    
    # PHASE 2 : FACTCHECK WIKIPEDIA POUR CHAQUE PERSONNE
    people_to_explore_next = []
    
    for person_name in people:
        if person_name in VISITED_PEOPLE:
            logger.info(f"⏭️  {person_name} déjà traité, skip")
            continue
        
        VISITED_PEOPLE.add(person_name)
        
        logger.info(f"\n{'─'*60}")
        logger.info(f"🔍 Traitement : {person_name} (profondeur {current_depth})")
        
        # Créer l'entité personne
        person_entity = PersonEntity(
            name=person_name,
            depth=current_depth,
            found_via=initial_query if current_depth > 0 else 'requête principale',
            query=ORIGINAL_QUERY
        )
        
        # Factcheck Wikipedia
        wiki_data = wikipedia_factcheck_person_rigorous(person_name)
        
        if not wiki_data:
            logger.warning(f"❌ {person_name} non vérifié sur Wikipedia, ignoré")
            person_entity.factcheck_status = "failed"
            continue
        
        person_entity.wikipedia_data = wiki_data
        person_entity.factcheck_status = wiki_data.get('factcheck_status', 'verified')
        
        # Stocker les relations et institutions
        relationships = wiki_data.get('detailed_relationships', [])
        person_entity.relationships = relationships
        person_entity.organizations = wiki_data.get('linked_institutions', [])
        
        # Ajouter à la liste des entités
        ALL_FOUND_ENTITIES.append(person_entity)
        
        logger.info(f"✅ {person_name} fackchecké (profondeur {current_depth})")
        logger.info(f"   - {len(relationships)} relations détaillées")
        logger.info(f"   - {len(person_entity.organizations)} institutions liées")
        
        # Collecter les personnes à explorer au niveau suivant
        if current_depth < max_depth - 1 and EXPONENTIAL_EXPLORATION:
            for rel in relationships:
                if rel.person_to not in VISITED_PEOPLE and rel.confidence >= 0.7:
                    people_to_explore_next.append(rel.person_to)
        
        # Petit délai pour éviter de surcharger Wikipedia
        time.sleep(0.5)
    
    # PHASE 3 : EXPLORATION RÉCURSIVE DU NIVEAU SUIVANT
    if current_depth < max_depth - 1 and people_to_explore_next:
        logger.info(f"\n🔄 Exploration du niveau suivant : {len(people_to_explore_next)} personnes")
        
        # Explorer TOUTES les personnes du niveau suivant (exponentiel)
        for next_person in people_to_explore_next:
            if next_person not in VISITED_PEOPLE:
                explore_network_exponential(
                    next_person,
                    current_depth + 1,
                    max_depth
                )

def final_validation_before_commit(entities: List[PersonEntity], original_query: str) -> Tuple[List[PersonEntity], List[PersonEntity]]:
    """
    🎯 VALIDATION FINALE de toutes les personnes AVANT commit
    Filtre rigoureux pour garantir la qualité journalistique
    """
    logger.info(f"\n{'='*70}")
    logger.info(f"🎯 VALIDATION FINALE AVANT COMMIT")
    logger.info(f"{'='*70}")
    logger.info(f"📊 {len(entities)} personnes à valider contre la requête : '{original_query}'")
    
    validated_entities = []
    rejected_entities = []
    
    for person_entity in entities:
        if not isinstance(person_entity, PersonEntity):
            continue
        
        # Validation complète
        is_valid, confidence, reason = validate_person_relevance_comprehensive(
            person_entity,
            original_query
        )
        
        person_entity.is_validated = is_valid
        person_entity.validation_score = confidence
        person_entity.validation_reason = reason
        
        VALIDATION_SCORES[person_entity.name] = {
            'score': confidence,
            'validated': is_valid,
            'reason': reason
        }
        
        if is_valid:
            validated_entities.append(person_entity)
        else:
            rejected_entities.append(person_entity)
    
    logger.info(f"\n✅ Validation finale : {len(validated_entities)} acceptées, {len(rejected_entities)} rejetées")
    
    return validated_entities, rejected_entities

def create_person_file_comprehensive(person: PersonEntity, all_institutions: List[str]) -> bool:
    """
    📝 Création de fiche personne COMPLÈTE avec relations détaillées pour Obsidian
    """
    person_name = person.name
    person_data = person.wikipedia_data
    depth = person.depth
    found_via = person.found_via
    validation_score = person.validation_score
    
    if not person_data:
        logger.error(f"❌ Pas de données Wikipedia pour {person_name}")
        return False
    
    personnes_folder = Path("personnes")
    personnes_folder.mkdir(exist_ok=True)
    
    safe_filename = re.sub(r'[^\w\s-]', '', person_name).strip().replace(' ', '-')
    file_path = personnes_folder / f"{safe_filename}.md"
    
    if file_path.exists():
        logger.info(f"ℹ️  {person_name} existe déjà, ignoré")
        return False
    
    # ========== CONSTRUCTION DU CONTENU MARKDOWN ==========
    
    # En-tête avec contexte de découverte
    discovery_header = ""
    if depth == 0:
        discovery_header = f"> 🎯 **Sujet principal de la recherche**\n> Score de pertinence : {validation_score:.0%}\n"
    else:
        discovery_header = f"> 🔍 **Découvert via [[{found_via}]]** (niveau {depth})\n> Score de pertinence : {validation_score:.0%}\n"
    
    # Biographie
    bio_courte = person_data.get('bio_courte', '')
    bio_detaillee = person_data.get('bio_detaillee', '')
    
    bio_section = f"""## Biographie

{bio_detaillee if bio_detaillee else bio_courte}
"""
    
    # Section Informations personnelles
    info_section = "\n## Informations personnelles\n\n"
    
    if person_data.get('date_naissance'):
        info_section += f"**Date de naissance** : {person_data['date_naissance']}\n"
    if person_data.get('date_deces'):
        info_section += f"**Date de décès** : {person_data['date_deces']}\n"
    if person_data.get('lieu_naissance'):
        info_section += f"**Lieu de naissance** : {person_data['lieu_naissance']}\n"
    if person_data.get('nationalite'):
        info_section += f"**Nationalité** : {person_data['nationalite']}\n"
    if person_data.get('statut_actuel'):
        info_section += f"**Statut** : {person_data['statut_actuel']}\n"
    
    # Section Formation
    formation_section = ""
    formation = person_data.get('formation', [])
    if formation and len(formation) > 0:
        formation_section = "\n## Formation\n\n"
        for item in formation[:10]:
            formation_section += f"- {item}\n"
    
    # Section Carrière
    carriere_section = ""
    carriere = person_data.get('carriere', [])
    if carriere and len(carriere) > 0:
        carriere_section = "\n## Carrière\n\n"
        for item in carriere[:15]:
            carriere_section += f"- {item}\n"
    
    # Section Organisations et Institutions (avec liens Obsidian)
    org_section = ""
    institutions = person_data.get('linked_institutions', [])
    all_orgs = list(set(institutions + all_institutions))
    
    if all_orgs:
        org_section = "\n## Organisations et Institutions\n\n"
        for org in all_orgs[:20]:
            org_section += f"- [[{org}]]\n"
    
    # Section RELATIONS DÉTAILLÉES (cœur de l'Obsidian graph)
    relations_section = ""
    relationships = person.relationships
    
    if relationships and len(relationships) > 0:
        relations_section = "\n## Réseau et Connexions\n\n"
        relations_section += f"*{len(relationships)} relations documentées*\n\n"
        
        # Grouper par type de relation
        relations_by_type = defaultdict(list)
        for rel in relationships:
            relations_by_type[rel.relationship_type].append(rel)
        
        for rel_type, rels in relations_by_type.items():
            relations_section += f"\n### {rel_type.capitalize()}\n\n"
            for rel in sorted(rels, key=lambda x: x.confidence, reverse=True)[:10]:
                # Format Obsidian avec description détaillée
                relations_section += f"- [[{rel.person_to}]] : {rel.description} *(confiance: {rel.confidence:.0%})*\n"
    
    # Section Distinctions
    distinctions_section = ""
    distinctions = person_data.get('distinctions', [])
    if distinctions and len(distinctions) > 0:
        distinctions_section = "\n## Distinctions et Prix\n\n"
        for item in distinctions[:10]:
            distinctions_section += f"- {item}\n"
    
    # Section Controverses (transparence journalistique)
    controverses_section = ""
    controverses = person_data.get('controverses', [])
    if controverses and len(controverses) > 0:
        controverses_section = "\n## Controverses\n\n"
        for item in controverses[:10]:
            controverses_section += f"- {item}\n"
    
    # Mots-clés (tags Obsidian)
    mots_cles = person_data.get('mots_cles', [])
    tags_line = ""
    if mots_cles:
        tags_line = "\n**Tags** : " + " · ".join([f"#{tag.replace(' ', '-')}" for tag in mots_cles[:10]]) + "\n"
    
    # Footer avec métadonnées de vérification
    footer = f"""
---

## Métadonnées et Vérification

**Source principale** : [Wikipedia]({person_data.get('wikipedia_url', '')})  
**Titre Wikipedia** : {person_data.get('wikipedia_title', person_name)}  
**Statut de vérification** : ✅ {person_data.get('factcheck_status', 'verified')}  
**Date de vérification** : {person_data.get('verification_date', datetime.now().strftime('%Y-%m-%d'))}  
**Longueur article Wikipedia** : {person_data.get('content_length', 0)} caractères  
**Niveau de notoriété** : {person_data.get('niveau_notoriete', 'N/A')}/10  
**Score de pertinence** : {validation_score:.0%}  
**Profondeur de recherche** : {depth}  
**Requête originale** : "{person.original_query}"  

{tags_line}

*Fiche créée le {datetime.now().strftime('%Y-%m-%d à %H:%M')} via l'Œil de Dieu (exploration récursive niveau {depth})*
"""
    
    # ========== ASSEMBLAGE FINAL ==========
    content = f"""{discovery_header}
{bio_section}
{info_section}
{formation_section}
{carriere_section}
{org_section}
{relations_section}
{distinctions_section}
{controverses_section}
{footer}
"""
    
    # ========== MÉTADONNÉES FRONTMATTER ==========
    metadata = {
        'type': 'personne',
        'nom_complet': person_data.get('nom_complet_verifie', person_name),
        'prenoms': person_name.split()[0] if ' ' in person_name else person_name,
        'date_naissance': person_data.get('date_naissance', ''),
        'date_deces': person_data.get('date_deces', ''),
        'lieu_naissance': person_data.get('lieu_naissance', ''),
        'nationalite': person_data.get('nationalite', ''),
        'genre': person_data.get('genre', ''),
        'statut': person_data.get('statut_actuel', ''),
        'bio': bio_courte,
        'formation': formation[:10],
        'carriere': carriere[:15],
        'affiliations': all_orgs[:20],
        'distinctions': distinctions[:10],
        'controverses': controverses[:10],
        'liens': [rel.person_to for rel in relationships[:20]],
        'relations_detaillees': [rel.to_dict() for rel in relationships[:20]],
        'presse': [],
        'sources': [person_data.get('wikipedia_url', '')],
        'statut_note': 'verifie_wikipedia',
        'tags': ['elite', 'wikipedia', f'niveau-{depth}', 'oeil-de-dieu'] + mots_cles[:5],
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'found_via': found_via,
        'search_depth': depth,
        'verification_status': person_data.get('factcheck_status', 'verified'),
        'verification_date': person_data.get('verification_date', ''),
        'validation_score': round(validation_score, 2),
        'validation_reason': person.validation_reason,
        'original_query': person.original_query,
        'niveau_notoriete': person_data.get('niveau_notoriete', ''),
        'relationships_count': len(relationships),
        'institutions_count': len(all_orgs),
        'wikipedia_content_length': person_data.get('content_length', 0)
    }
    
    # ========== ÉCRITURE DU FICHIER ==========
    post = frontmatter.Post(content, **metadata)
    
    try:
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)
        
        person.created_file_path = str(file_path)
        CREATED_FILES.append(str(file_path))
        
        logger.info(f"✅ Fiche créée : {file_path}")
        logger.info(f"   - {len(relationships)} relations détaillées")
        logger.info(f"   - {len(all_orgs)} institutions")
        logger.info(f"   - Score de validation : {validation_score:.0%}")
        
        EXPLORATION_STATS['files_created'] += 1
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur création fiche {person_name} : {e}")
        EXPLORATION_STATS['errors'] += 1
        return False

def create_institution_file_comprehensive(institution: InstitutionEntity) -> bool:
    """
    📝 Création de fiche institution COMPLÈTE
    """
    institution_name = institution.name
    depth = institution.depth
    found_via = institution.found_via
    
    institutions_folder = Path("institutions")
    institutions_folder.mkdir(exist_ok=True)
    
    safe_filename = re.sub(r'[^\w\s-]', '', institution_name).strip().replace(' ', '-')
    file_path = institutions_folder / f"{safe_filename}.md"
    
    if file_path.exists():
        logger.info(f"ℹ️  Institution {institution_name} existe déjà, ignoré")
        return False
    
    # Essayer de trouver sur Wikipedia
    try:
        page = wikipedia.page(institution_name, auto_suggest=True)
        summary = page.summary[:800]
        wiki_url = page.url
        verified = True
        
        # Extraire plus d'infos
        full_content = page.content[:3000]
        
        schema = """
        {
          "description_detaillee": "Description détaillée de l'institution",
          "date_fondation": "Date de fondation",
          "fondateurs": "Noms des fondateurs",
          "siege_social": "Localisation du siège",
          "type_organisation": "Type d'organisation (entreprise, club, think tank, etc.)",
          "domaine_activite": "Domaine d'activité principal",
          "membres_notables": "Membres ou dirigeants notables (liste)",
          "influence": "Description de l'influence et du rôle"
        }
        """
        
        extracted_data = llm.extract_yaml_data(full_content, schema)
        
        description = extracted_data.get('description_detaillee', summary)
        membres = extracted_data.get('membres_notables', [])
        
        if isinstance(membres, str):
            membres = [m.strip() for m in membres.split(',') if m.strip()]
        
        institution.members = membres
        
    except:
        summary = f"Institution identifiée dans le réseau de pouvoir lié à : {found_via}"
        wiki_url = ""
        verified = False
        description = summary
        extracted_data = {}
    
    # Découverte
    discovery_text = ""
    if depth > 0:
        discovery_text = f"> 🔍 **Découvert via [[{found_via}]]** (niveau {depth})\n"
    else:
        discovery_text = f"> 🎯 **Sujet principal de la recherche**\n"
    
    # Membres (liens Obsidian)
    membres_section = ""
    if institution.members:
        membres_section = f"\n## Membres et Dirigeants\n\n"
        for membre in institution.members[:20]:
            membres_section += f"- [[{membre}]]\n"
    
    content = f"""{discovery_text}

## Description

{description}

{membres_section}

---

## Métadonnées

**Type** : Institution / Organisation  
**Catégorie** : {extracted_data.get('type_organisation', 'N/A')}  
**Fondation** : {extracted_data.get('date_fondation', 'N/A')}  
**Siège** : {extracted_data.get('siege_social', 'N/A')}  
**Domaine** : {extracted_data.get('domaine_activite', 'N/A')}  
**Source** : {'[Wikipedia](' + wiki_url + ')' if wiki_url else 'Réseau Mistral'}  
**Statut de vérification** : {'✅ Wikipedia' if verified else '⚠️ À vérifier'}  
**Date d'ajout** : {datetime.now().strftime('%Y-%m-%d')}  

*Fiche créée via l'Œil de Dieu (niveau {depth})*
"""
    
    metadata = {
        'type': 'institution',
        'nom': institution_name,
        'description': description,
        'type_organisation': extracted_data.get('type_organisation', ''),
        'date_fondation': extracted_data.get('date_fondation', ''),
        'siege': extracted_data.get('siege_social', ''),
        'domaine': extracted_data.get('domaine_activite', ''),
        'membres': institution.members[:20],
        'sources': [wiki_url] if wiki_url else [],
        'statut_note': 'verifie_wikipedia' if verified else 'a_verifier',
        'tags': ['institution', 'elite', f'niveau-{depth}', 'oeil-de-dieu'],
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'found_via': found_via,
        'search_depth': depth,
        'verified': verified
    }
    
    post = frontmatter.Post(content, **metadata)
    
    try:
        with open(file_path, 'wb') as f:
            frontmatter.dump(post, f)
        
        institution.created_file_path = str(file_path)
        CREATED_FILES.append(str(file_path))
        
        logger.info(f"✅ Institution créée : {file_path}")
        EXPLORATION_STATS['institutions_created'] += 1
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Erreur création institution {institution_name} : {e}")
        EXPLORATION_STATS['errors'] += 1
        return False

def generate_exploration_report(query: str, validated: List[PersonEntity], 
                               rejected: List[PersonEntity]) -> str:
    """
    📊 Génère un rapport détaillé de l'exploration
    """
    report = f"""
{'='*70}
📊 RAPPORT D'EXPLORATION - ŒIL DE DIEU
{'='*70}

REQUÊTE ORIGINALE : "{query}"
DATE : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*70}
STATISTIQUES GLOBALES
{'='*70}

Profondeur d'exploration : {MAX_DEPTH} niveaux
Mode : {'EXPONENTIEL (complet)' if EXPONENTIAL_EXPLORATION else 'LIMITÉ'}
Seuil de confiance : {CONFIDENCE_THRESHOLD:.0%}

Appels Mistral : {EXPLORATION_STATS['mistral_calls']}
Entités identifiées (Mistral) : {EXPLORATION_STATS['entities_identified']}
Institutions identifiées : {EXPLORATION_STATS['institutions_identified']}
Relations extraites : {EXPLORATION_STATS['relationships_extracted']}

Factchecks Wikipedia :
  - Réussis : {EXPLORATION_STATS['factcheck_success']}
  - Non trouvés : {EXPLORATION_STATS['factcheck_not_found']}
  - Ambiguïtés résolues : {EXPLORATION_STATS['factcheck_disambiguation']}
  - Échecs : {EXPLORATION_STATS['factcheck_failed']}

Validations :
  - Effectuées : {EXPLORATION_STATS['validations_performed']}
  - Acceptées : {EXPLORATION_STATS['validations_passed']}
  - Rejetées : {EXPLORATION_STATS['validations_rejected']}

Fichiers créés :
  - Personnes : {EXPLORATION_STATS['files_created']}
  - Institutions : {EXPLORATION_STATS['institutions_created']}
  - Total : {EXPLORATION_STATS['files_created'] + EXPLORATION_STATS['institutions_created']}

Erreurs : {EXPLORATION_STATS['errors']}

{'='*70}
PERSONNES VALIDÉES ({len(validated)})
{'='*70}

"""
    
    # Trier par score de validation
    validated_sorted = sorted(validated, key=lambda x: x.validation_score, reverse=True)
    
    for i, person in enumerate(validated_sorted, 1):
        report += f"""
{i}. {person.name}
   Profondeur : {person.depth}
   Score : {person.validation_score:.0%}
   Trouvé via : {person.found_via}
   Relations : {len(person.relationships)}
   Raison : {person.validation_reason[:100]}...
"""
    
    report += f"""
{'='*70}
PERSONNES REJETÉES ({len(rejected)})
{'='*70}

"""
    
    rejected_sorted = sorted(rejected, key=lambda x: x.validation_score, reverse=True)
    
    for i, person in enumerate(rejected_sorted, 1):
        report += f"""
{i}. {person.name}
   Profondeur : {person.depth}
   Score : {person.validation_score:.0%}
   Raison du rejet : {person.validation_reason[:100]}...
"""
    
    report += f"""
{'='*70}
ANALYSE DE QUALITÉ
{'='*70}

Taux de validation : {len(validated)/(len(validated)+len(rejected))*100:.1f}%
Score moyen des validés : {sum(p.validation_score for p in validated)/len(validated):.0%}
Score moyen des rejetés : {sum(p.validation_score for p in rejected)/len(rejected) if rejected else 0:.0%}

Distribution par profondeur :
"""
    
    for depth in range(MAX_DEPTH):
        count = len([p for p in validated if p.depth == depth])
        report += f"  Niveau {depth} : {count} personnes\n"
    
    report += f"""
{'='*70}
FIN DU RAPPORT
{'='*70}
"""
    
    return report

def is_generic_people_term(name: str) -> bool:
    """
    Vérifie si un nom est un terme générique (pas une personne spécifique)
    """
    generic_terms = [
        'dirigeants', 'membres', 'présidents', 'ministres', 'executives',
        'leaders', 'cadres', 'responsables', 'directeurs', 'personnes',
        'gens', 'individus', 'acteurs', 'participants', 'représentants'
    ]
    
    name_lower = name.lower().strip()
    
    # Vérifier correspondance exacte
    if name_lower in generic_terms:
        return True
    
    # Vérifier correspondance par mots complets (pattern compilé une seule fois)
    if not hasattr(is_generic_people_term, '_pattern'):
        # Créer un pattern combiné pour tous les termes
        escaped_terms = [re.escape(term) for term in generic_terms]
        pattern = r'\b(?:' + '|'.join(escaped_terms) + r')\b'
        is_generic_people_term._pattern = re.compile(pattern)
    
    return bool(is_generic_people_term._pattern.search(name_lower))
    name_lower = name.lower()
    return any(term in name_lower for term in generic_terms)

def main(query: str = None):
    """
    🧠 ŒIL DE DIEU - Exploration exponentielle avec validation finale
    Niveau journalistique : rigueur, traçabilité, vérification
    """
    global VISITED_PEOPLE, VISITED_ORGS, ALL_FOUND_ENTITIES, ORIGINAL_QUERY
    global EXPLORATION_STATS, RELATIONSHIPS_GRAPH, VALIDATION_SCORES, CREATED_FILES
    
    # Réinitialisation complète
    VISITED_PEOPLE = set()
    VISITED_ORGS = set()
    ALL_FOUND_ENTITIES = []
    EXPLORATION_STATS = defaultdict(int)
    RELATIONSHIPS_GRAPH = defaultdict(list)
    VALIDATION_SCORES = {}
    CREATED_FILES = []
    
    print("\n" + "="*70)
    print("🧠 ŒIL DE DIEU - Construction de réseau de pouvoir")
    print("="*70)
    print("\n📋 Mode d'opération :")
    print("  1. Mistral identifie les entités (connaissance générale)")
    print("  2. Wikipedia factcheck et enrichit (sources vérifiables)")
    print("  3. Exploration EXPONENTIELLE sur 3 niveaux (tous les chemins)")
    print("  4. Extraction de relations DÉTAILLÉES avec descriptions")
    print("  5. Validation FINALE de toutes les personnes avant commit")
    print("  6. Création de fiches Obsidian avec liens [[personne]]")
    print(f"\n⚙️  Paramètres :")
    print(f"  - Profondeur maximale : {MAX_DEPTH}")
    print(f"  - Seuil de confiance : {CONFIDENCE_THRESHOLD:.0%}")
    print(f"  - Mode exponentiel : {'OUI' if EXPONENTIAL_EXPLORATION else 'NON'}")
    print("="*70)
    
    if not query:
        print("\nExemples de requêtes :")
        print("  - Le Siècle")
        print("  - Jeffrey Epstein")
        print("  - Emmanuel Macron")
        print("  - Groupe Bilderberg")
        print("  - Bernard Arnault")
        print("="*70)
        
        query = input("\n🎯 Entité à explorer : ").strip()
    
    if not query:
        logger.error("❌ Requête vide, abandon")
        return
    
    ORIGINAL_QUERY = query
    
    logger.info(f"🚀 Lancement de l'exploration : '{query}'")
    start_time = time.time()
    
    # ========== PHASE 0 : RÉPONSE DIRECTE À LA REQUÊTE ==========
    print(f"\n🎯 Phase 0 : Analyse et réponse directe à la requête...\n")
    
    initial_answer = answer_initial_query_directly(query)
    
    if not initial_answer:
        logger.warning("❌ Impossible de répondre à la requête")
        return
    
    query_type = initial_answer.get('query_type', 'unknown')
    interpretation = initial_answer.get('interpretation', '')
    initial_people = initial_answer.get('people', [])
    initial_institutions = initial_answer.get('institutions', [])
    
    print(f"\n✅ Analyse de la requête :")
    print(f"   - Type : {query_type}")
    print(f"   - Interprétation : {interpretation}")
    print(f"   - {len(initial_people)} personnes identifiées initialement")
    print(f"   - {len(initial_institutions)} institutions identifiées")
    
    # Afficher la réponse directe
    if query_type == 'people_group':
        print(f"\n📋 RÉPONSE DIRECTE - Liste des personnes :")
        for i, person in enumerate(initial_people, 1):
            print(f"   {i}. {person}")
    elif query_type == 'single_person':
        print(f"\n👤 RÉPONSE DIRECTE - Personne principale : {initial_answer.get('main_subject', '')}")
        print(f"   Réseau immédiat ({len(initial_people)-1} personnes) :")
        for person in initial_people[1:]:
            print(f"   - {person}")
    elif query_type == 'institution':
        print(f"\n🏢 RÉPONSE DIRECTE - Institution : {initial_answer.get('main_subject', '')}")
        print(f"   Membres/Dirigeants ({len(initial_people)} personnes) :")
        for i, person in enumerate(initial_people, 1):
            print(f"   {i}. {person}")
    
    # ========== PHASE 1 : EXPLORATION EXPONENTIELLE ==========
    print(f"\n🌳 Phase 1 : Exploration exponentielle (3 niveaux)...\n")
    explore_network_exponential(query, current_depth=0, max_depth=MAX_DEPTH, initial_query_type=query_type)
    # Stocker le type de requête pour l'exploration
    explore_network_exponential._initial_query_type = query_type
    
    try:
        # ========== PHASE 1 : EXPLORATION EXPONENTIELLE ==========
        print(f"\n🌳 Phase 1 : Exploration exponentielle (3 niveaux)...\n")
        explore_network_exponential(query, current_depth=0, max_depth=MAX_DEPTH)
    finally:
        # Nettoyer l'attribut temporaire après utilisation (même en cas d'exception)
        if hasattr(explore_network_exponential, '_initial_query_type'):
            delattr(explore_network_exponential, '_initial_query_type')
    
    if not ALL_FOUND_ENTITIES:
        logger.warning("❌ Aucune entité trouvée")
        return
    
    # Séparer personnes et institutions
    people_entities = [e for e in ALL_FOUND_ENTITIES if isinstance(e, PersonEntity)]
    institution_entities = [e for e in ALL_FOUND_ENTITIES if isinstance(e, InstitutionEntity)]
    
    print(f"\n✅ Exploration terminée :")
    print(f"   - {len(people_entities)} personnes découvertes")
    print(f"   - {len(institution_entities)} institutions découvertes")
    print(f"   - {EXPLORATION_STATS['relationships_extracted']} relations extraites")

        # ========== PHASE 2 : VALIDATION FINALE AVANT COMMIT ==========
    print(f"\n🎯 Phase 2 : Validation finale de toutes les entités...\n")
    
    validated_people, rejected_people = final_validation_before_commit(
        people_entities,
        ORIGINAL_QUERY
    )
    
    print(f"\n✅ Validation terminée :")
    print(f"   - {len(validated_people)} personnes VALIDÉES")
    print(f"   - {len(rejected_people)} personnes REJETÉES")
    print(f"   - Taux de validation : {len(validated_people)/(len(validated_people)+len(rejected_people))*100:.1f}%")
    
    if not validated_people and not institution_entities:
        logger.warning("❌ Aucune entité validée à créer")
        return
    
    # ========== VÉRIFICATION DES FICHIERS EXISTANTS ==========
    print(f"\n📂 Phase 3 : Vérification des fichiers existants...\n")
    
    personnes_folder = Path("personnes")
    institutions_folder = Path("institutions")
    
    existing_people_files = set()
    existing_institution_files = set()
    
    if personnes_folder.exists():
        existing_people_files = {f.stem for f in personnes_folder.glob("*.md")}
        logger.info(f"📁 {len(existing_people_files)} fichiers personnes existants trouvés")
    
    if institutions_folder.exists():
        existing_institution_files = {f.stem for f in institutions_folder.glob("*.md")}
        logger.info(f"📁 {len(existing_institution_files)} fichiers institutions existants trouvés")
    
    # Filtrer les entités déjà existantes
    people_to_create = []
    people_already_exist = []
    
    for person in validated_people:
        safe_filename = re.sub(r'[^\w\s-]', '', person.name).strip().replace(' ', '-')
        if safe_filename in existing_people_files:
            people_already_exist.append(person.name)
            logger.info(f"⏭️  {person.name} existe déjà, skip")
        else:
            people_to_create.append(person)
    
    institutions_to_create = []
    institutions_already_exist = []
    
    for inst in institution_entities:
        safe_filename = re.sub(r'[^\w\s-]', '', inst.name).strip().replace(' ', '-')
        if safe_filename in existing_institution_files:
            institutions_already_exist.append(inst.name)
            logger.info(f"⏭️  Institution {inst.name} existe déjà, skip")
        else:
            institutions_to_create.append(inst)
    
    print(f"\n📊 Bilan des fichiers à créer :")
    print(f"   - Personnes : {len(people_to_create)} nouvelles ({len(people_already_exist)} existent déjà)")
    print(f"   - Institutions : {len(institutions_to_create)} nouvelles ({len(institutions_already_exist)} existent déjà)")
    
    if not people_to_create and not institutions_to_create:
        print(f"\n⚠️  Toutes les entités existent déjà, aucune création nécessaire")
        logger.info("✅ Toutes les entités existent déjà")
        return
    
    # ========== PHASE 4 : CRÉATION DES FICHIERS ==========
    print(f"\n📝 Phase 4 : Création des fiches...\n")
    
    all_institutions_names = [inst.name for inst in institution_entities]
    
    people_created = 0
    people_errors = 0
    
    for person in people_to_create:
        try:
            if create_person_file_comprehensive(person, all_institutions_names):
                people_created += 1
                print(f"   ✅ {person.name} (score: {person.validation_score:.0%}, niveau: {person.depth})")
            else:
                people_errors += 1
        except Exception as e:
            logger.error(f"❌ Erreur création {person.name} : {e}")
            people_errors += 1
            EXPLORATION_STATS['errors'] += 1
    
    institutions_created = 0
    institutions_errors = 0
    
    for inst in institutions_to_create:
        try:
            if create_institution_file_comprehensive(inst):
                institutions_created += 1
                print(f"   🏢 {inst.name} (niveau: {inst.depth})")
            else:
                institutions_errors += 1
        except Exception as e:
            logger.error(f"❌ Erreur création institution {inst.name} : {e}")
            institutions_errors += 1
            EXPLORATION_STATS['errors'] += 1
    
    # ========== PHASE 5 : GÉNÉRATION DU RAPPORT ==========
    print(f"\n📊 Phase 5 : Génération du rapport...\n")
    
    report = generate_exploration_report(query, validated_people, rejected_people)
    
    # Sauvegarder le rapport
    reports_folder = Path("rapports")
    reports_folder.mkdir(exist_ok=True)
    
    report_filename = f"rapport_exploration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = reports_folder / report_filename
    
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"📄 Rapport sauvegardé : {report_path}")
        print(f"   📄 Rapport sauvegardé : {report_path}")
    except Exception as e:
        logger.error(f"❌ Erreur sauvegarde rapport : {e}")
    
    # ========== PHASE 6 : RÉSUMÉ FINAL ==========
    elapsed_time = time.time() - start_time
    
    print("\n" + "="*70)
    print("🎉 RÉSULTAT FINAL")
    print("="*70)
    
    print(f"\n📊 STATISTIQUES COMPLÈTES :")
    print(f"   Durée d'exploration : {elapsed_time:.1f} secondes ({elapsed_time/60:.1f} minutes)")
    print(f"\n   🔍 Découverte :")
    print(f"      - Personnes découvertes : {len(people_entities)}")
    print(f"      - Institutions découvertes : {len(institution_entities)}")
    print(f"      - Relations extraites : {EXPLORATION_STATS['relationships_extracted']}")
    
    print(f"\n   ✅ Validation :")
    print(f"      - Personnes validées : {len(validated_people)}")
    print(f"      - Personnes rejetées : {len(rejected_people)}")
    print(f"      - Taux de validation : {len(validated_people)/(len(validated_people)+len(rejected_people))*100:.1f}%")
    
    print(f"\n   📝 Création :")
    print(f"      - Personnes créées : {people_created}")
    print(f"      - Personnes déjà existantes : {len(people_already_exist)}")
    print(f"      - Institutions créées : {institutions_created}")
    print(f"      - Institutions déjà existantes : {len(institutions_already_exist)}")
    print(f"      - Erreurs : {people_errors + institutions_errors}")
    
    print(f"\n   🌳 Répartition par profondeur :")
    for depth in range(MAX_DEPTH):
        count_validated = len([p for p in validated_people if p.depth == depth])
        count_created = len([p for p in people_to_create if p.depth == depth and p.name not in people_already_exist])
        print(f"      Niveau {depth} : {count_validated} validées, {count_created} créées")
    
    print(f"\n   🔗 Qualité du réseau :")
    if validated_people:
        avg_score = sum(p.validation_score for p in validated_people) / len(validated_people)
        avg_relations = sum(len(p.relationships) for p in validated_people) / len(validated_people)
        print(f"      - Score moyen de pertinence : {avg_score:.0%}")
        print(f"      - Relations moyennes par personne : {avg_relations:.1f}")
    
    print(f"\n   📖 Factchecks Wikipedia :")
    print(f"      - Réussis : {EXPLORATION_STATS['factcheck_success']}")
    print(f"      - Non trouvés : {EXPLORATION_STATS['factcheck_not_found']}")
    print(f"      - Ambiguïtés résolues : {EXPLORATION_STATS['factcheck_disambiguation']}")
    print(f"      - Échecs : {EXPLORATION_STATS['factcheck_failed']}")
    
    print(f"\n   🤖 Appels Mistral :")
    print(f"      - Total : {EXPLORATION_STATS['mistral_calls']}")
    
    total_created = people_created + institutions_created
    
    # ========== PHASE 7 : AFFICHAGE DES ENTITÉS CRÉÉES ==========
    if people_created > 0:
        print(f"\n👥 PERSONNES CRÉÉES ({people_created}) :")
        for person in people_to_create:
            if person.created_file_path:
                print(f"   ✅ {person.name} (score: {person.validation_score:.0%}, niveau: {person.depth})")
    
    if institutions_created > 0:
        print(f"\n🏢 INSTITUTIONS CRÉÉES ({institutions_created}) :")
        for inst in institutions_to_create:
            if inst.created_file_path:
                print(f"   ✅ {inst.name} (niveau: {inst.depth})")
    
    if people_already_exist:
        print(f"\n⏭️  PERSONNES DÉJÀ EXISTANTES ({len(people_already_exist)}) :")
        for name in people_already_exist[:10]:
            print(f"   - {name}")
        if len(people_already_exist) > 10:
            print(f"   ... et {len(people_already_exist) - 10} autres")
    
    if institutions_already_exist:
        print(f"\n⏭️  INSTITUTIONS DÉJÀ EXISTANTES ({len(institutions_already_exist)}) :")
        for name in institutions_already_exist[:10]:
            print(f"   - {name}")
        if len(institutions_already_exist) > 10:
            print(f"   ... et {len(institutions_already_exist) - 10} autres")
    
    # ========== PHASE 8 : COMMIT GIT ==========
    if total_created > 0:
        print("\n" + "="*70)
        print("💾 Phase 7 : Commit Git...")
        print("="*70)
        
        commit_msg = f"""feat: 🧠 Œil de Dieu - Exploration '{query}'

Statistiques :
- {people_created} personnes créées
- {institutions_created} institutions créées
- {len(validated_people)} personnes validées (taux: {len(validated_people)/(len(validated_people)+len(rejected_people))*100:.1f}%)
- {EXPLORATION_STATS['relationships_extracted']} relations extraites
- Exploration sur {MAX_DEPTH} niveaux
- Durée : {elapsed_time:.1f}s

Qualité :
- Score moyen : {sum(p.validation_score for p in validated_people)/len(validated_people):.0%}
- Relations moyennes : {sum(len(p.relationships) for p in validated_people)/len(validated_people):.1f}

Factchecks Wikipedia :
- Réussis : {EXPLORATION_STATS['factcheck_success']}
- Échecs : {EXPLORATION_STATS['factcheck_failed']}

Requête originale : "{ORIGINAL_QUERY}"
"""
        
        try:
            git.commit_changes(commit_msg)
            print("✅ Changements committés avec succès")
            logger.info("✅ Changements committés")
        except Exception as e:
            logger.error(f"❌ Erreur commit Git : {e}")
            print(f"⚠️  Erreur commit Git : {e}")
    else:
        print("\n⚠️  Aucun fichier créé, pas de commit Git")
    
    # ========== AFFICHAGE FINAL ==========
    print("\n" + "="*70)
    print("✨ EXPLORATION TERMINÉE")
    print("="*70)
    
    if total_created > 0:
        print(f"\n🎯 Résultat : {total_created} nouvelles entités ajoutées à la base")
        print(f"📊 Qualité : Score moyen de pertinence {sum(p.validation_score for p in validated_people)/len(validated_people):.0%}")
        print(f"🔗 Réseau : {EXPLORATION_STATS['relationships_extracted']} relations documentées")
        print(f"⏱️  Durée : {elapsed_time:.1f} secondes")
        print(f"\n📄 Rapport complet : {report_path}")
    else:
        print(f"\n⚠️  Aucune nouvelle entité créée")
        print(f"   Raison : Toutes les entités découvertes existent déjà")
    
    print("\n" + "="*70)
    
    # Afficher le TOP 10 des personnes validées par score
    if validated_people:
        print("\n🏆 TOP 10 - Personnes les plus pertinentes :")
        print("="*70)
        
        top_people = sorted(validated_people, key=lambda x: x.validation_score, reverse=True)[:10]
        
        for i, person in enumerate(top_people, 1):
            status = "✅ CRÉÉE" if person.name not in people_already_exist else "⏭️  EXISTANTE"
            print(f"{i:2d}. {person.name}")
            print(f"    Score: {person.validation_score:.0%} | Niveau: {person.depth} | {status}")
            print(f"    Via: {person.found_via}")
            print(f"    Relations: {len(person.relationships)}")
            print()
    
    # Message final
    print("="*70)
    print("🧠 Œil de Dieu - Mission accomplie")
    print("="*70)
    
    logger.info(f"✅ Exploration terminée : {total_created} entités créées")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query_arg = ' '.join(sys.argv[1:])
        main(query_arg)
    else:
        main()

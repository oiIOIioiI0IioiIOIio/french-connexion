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

# Variables globales pour tracker les personnes déjà traitées
VISITED_PEOPLE = set()
ALL_FOUND_PEOPLE = []

def extract_organization_from_query(query: str) -> list:
    """
    Extrait les noms d'organisations/institutions de la requête pour créer des liens
    Ex: "les PDG du CAC 40" → ["CAC 40"]
    Ex: "dirigeant du Groupe EBRA" → ["Groupe EBRA"]
    """
    logger.info(f"🔍 Extraction des organisations de la requête : {query}")
    
    prompt = f"""
Tu es un expert en extraction d'entités.

REQUÊTE : "{query}"

Extrais TOUS les noms d'organisations, institutions, entreprises, groupes mentionnés dans cette requête.

EXEMPLES :
- "les PDG du CAC 40" → ["CAC 40"]
- "les présidents de la 5e république" → ["Cinquième République"]
- "dirigeant du Groupe EBRA" → ["Groupe EBRA"]
- "ministres de l'économie français" → ["Ministère de l'Économie"]
- "membres du Siècle" → ["Le Siècle"]
- "Jeffrey Epstein" → []

Retourne un JSON avec :
- "organizations": liste de noms d'organisations (ou liste vide si aucune)

Format: {{"organizations": ["Nom1", "Nom2"]}}
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            orgs = result.get('organizations', [])
            logger.info(f"✅ Organisations extraites : {orgs}")
            return orgs
        
        return []
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction d'organisations : {e}")
        return []

def extract_main_subject_from_query(query: str) -> str:
    """
    Extrait le sujet principal de la requête (personne ou organisation)
    Ex: "Jeffrey Epstein" → "Jeffrey Epstein"
    Ex: "les PDG du CAC 40" → "CAC 40" (pour créer une fiche organisation)
    """
    logger.info(f"🎯 Extraction du sujet principal de la requête : {query}")
    
    prompt = f"""
Tu es un expert en analyse de requêtes.

REQUÊTE : "{query}"

Identifie le SUJET PRINCIPAL de cette requête :
- Si c'est une personne spécifique, retourne son nom complet
- Si c'est une organisation/institution, retourne son nom
- Si c'est un groupe de personnes (ex: "les PDG du CAC 40"), retourne l'organisation principale

EXEMPLES :
- "Jeffrey Epstein" → "Jeffrey Epstein"
- "les PDG du CAC 40" → "CAC 40"
- "dirigeant du Groupe EBRA" → "Groupe EBRA"
- "Emmanuel Macron" → "Emmanuel Macron"
- "membres du Siècle" → "Le Siècle"

Retourne un JSON avec :
- "subject": le nom du sujet principal
- "type": "personne" ou "organisation"

Format: {{"subject": "Nom", "type": "personne"}}
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            subject = result.get('subject', '')
            subject_type = result.get('type', 'personne')
            logger.info(f"✅ Sujet principal : {subject} (type: {subject_type})")
            return subject, subject_type
        
        return "", "personne"
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction du sujet : {e}")
        return "", "personne"

def search_people_on_wikipedia_recursive(query: str, current_depth: int = 0, max_depth: int = 3) -> list:
    """
    Recherche récursive sur Wikipedia avec exploration en profondeur
    """
    global VISITED_PEOPLE, ALL_FOUND_PEOPLE
    
    if current_depth >= max_depth:
        logger.info(f"🛑 Profondeur maximale atteinte ({max_depth})")
        return []
    
    logger.info(f"🔍 Recherche Wikipedia (profondeur {current_depth + 1}/{max_depth}) pour : {query}")
    
    try:
        search_results = wikipedia.search(query, results=5)
        
        if not search_results:
            logger.warning(f"Aucun résultat trouvé pour : {query}")
            return []
        
        page = wikipedia.page(search_results[0], auto_suggest=False)
        content = page.content
        
        logger.info(f"📄 Page trouvée : {page.title}")
        
        # Ajouter la personne principale si pas déjà visitée
        people_list = []
        if page.title not in VISITED_PEOPLE:
            people_list.append({
                'name': page.title,
                'depth': current_depth,
                'found_via': query if current_depth > 0 else 'requête principale'
            })
            VISITED_PEOPLE.add(page.title)
            logger.info(f"✅ Personne principale ajoutée : {page.title} (profondeur {current_depth})")
        
        # Extraire les personnes liées
        related_people = extract_people_from_text(content, query)
        
        # Ajouter les personnes liées
        for person in related_people:
            if person not in VISITED_PEOPLE:
                people_list.append({
                    'name': person,
                    'depth': current_depth + 1,
                    'found_via': page.title
                })
                VISITED_PEOPLE.add(person)
        
        ALL_FOUND_PEOPLE.extend(people_list)
        
        # Explorer récursivement les personnes liées (si profondeur < max)
        if current_depth < max_depth - 1:
            logger.info(f"🌳 Exploration des {len(related_people)} personnes liées...")
            for person in related_people[:5]:  # Limiter à 5 personnes par niveau
                if person not in VISITED_PEOPLE:
                    search_people_on_wikipedia_recursive(person, current_depth + 1, max_depth)
        
        return people_list
        
    except wikipedia.exceptions.PageError:
        logger.warning(f"Page Wikipedia non trouvée pour : {query}")
        return []
    except wikipedia.exceptions.DisambiguationError as e:
        logger.warning(f"Page ambiguë pour '{query}'. Options : {e.options[:3]}")
        try:
            page = wikipedia.page(e.options[0])
            content = page.content
            
            people_list = []
            if page.title not in VISITED_PEOPLE:
                people_list.append({
                    'name': page.title,
                    'depth': current_depth,
                    'found_via': query if current_depth > 0 else 'requête principale'
                })
                VISITED_PEOPLE.add(page.title)
            
            related_people = extract_people_from_text(content, query)
            for person in related_people:
                if person not in VISITED_PEOPLE:
                    people_list.append({
                        'name': person,
                        'depth': current_depth + 1,
                        'found_via': page.title
                    })
                    VISITED_PEOPLE.add(person)
            
            ALL_FOUND_PEOPLE.extend(people_list)
            
            if current_depth < max_depth - 1:
                for person in related_people[:5]:
                    if person not in VISITED_PEOPLE:
                        search_people_on_wikipedia_recursive(person, current_depth + 1, max_depth)
            
            return people_list
        except:
            return []
    except Exception as e:
        logger.error(f"Erreur lors de la recherche Wikipedia : {e}")
        return []

def extract_people_from_text(text: str, original_query: str) -> list:
    """
    Utilise Mistral pour extraire les noms des personnes
    """
    logger.info("🤖 Extraction des noms de personnes via Mistral...")
    
    if len(text) > 8000:
        text = text[:8000]
    
    prompt = f"""
Tu es un assistant spécialisé dans l'extraction de noms de personnes depuis des textes Wikipedia.

REQUÊTE ORIGINALE : "{original_query}"

À partir du texte Wikipedia ci-dessous, extrais une liste de noms complets de personnes 
qui sont mentionnées de manière significative (pas juste en passant).

RÈGLES :
- Retourne UNIQUEMENT les noms complets (Prénom Nom)
- N'inclus que des personnes réelles (pas de personnages fictifs)
- Maximum 15 personnes
- Privilégie les personnes avec des relations importantes (collaborateurs, famille, associés)
- Format : liste JSON sous la clé "names": ["Nom1", "Nom2", ...]
- Si aucune personne trouvée, retourne {{"names": []}}

TEXTE WIKIPEDIA :
{text}

Retourne un objet JSON avec la clé "names" contenant la liste :
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            
            if isinstance(result, dict):
                people = result.get('names', result.get('personnes', result.get('list', [])))
            else:
                people = result
            
            logger.info(f"✅ {len(people)} personnes extraites")
            return people
        
        return []
        
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction de noms : {e}")
        return []

def validate_person_relevance(person_name: str, original_query: str, depth: int) -> tuple:
    """
    Valide que la personne correspond bien à la requête originale via Mistral
    Retourne (True/False, raison)
    """
    logger.info(f"🔍 Validation de pertinence : {person_name} (profondeur {depth})")
    
    # Si c'est la personne principale (depth 0), toujours valider
    if depth == 0:
        return (True, "Sujet principal de la requête")
    
    prompt = f"""
Tu es un expert en validation de données.

REQUÊTE ORIGINALE : "{original_query}"
PERSONNE À VALIDER : "{person_name}"
PROFONDEUR DE RECHERCHE : {depth} (0 = personne principale, 1-3 = personnes liées)

Ta mission : déterminer si cette personne est PERTINENTE pour la requête.

CRITÈRES DE VALIDATION :
- Profondeur 0 : TOUJOURS valider (c'est le sujet principal)
- Profondeur 1 : Valider si lien DIRECT et SIGNIFICATIF avec la requête
- Profondeur 2-3 : Valider si lien IMPORTANT (famille proche, associés directs, collaborateurs clés)

EXEMPLES :
- Requête "Jeffrey Epstein" + Profondeur 0 + "Jeffrey Epstein" → OUI (sujet principal)
- Requête "Jeffrey Epstein" + Profondeur 1 + "Ghislaine Maxwell" → OUI (associée directe)
- Requête "Jeffrey Epstein" + Profondeur 2 + "Bill Clinton" → OUI (relation documentée)
- Requête "Jeffrey Epstein" + Profondeur 3 + "Barack Obama" → NON (lien trop indirect)
- Requête "les présidents de la 5e république" + Profondeur 1 + "Emmanuel Macron" → OUI

Retourne un JSON avec :
- "valid": true ou false
- "reason": explication courte (1 phrase)

Sois STRICT pour profondeur 2-3, SOUPLE pour profondeur 0-1.
"""
    
    try:
        chat_response = llm.client.chat.complete(
            model=llm.model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        if chat_response.choices and chat_response.choices[0].message:
            result = json.loads(chat_response.choices[0].message.content)
            is_valid = result.get('valid', False)
            reason = result.get('reason', 'Pas de raison fournie')
            
            if is_valid:
                logger.info(f"✅ {person_name} → VALIDÉ (profondeur {depth})")
            else:
                logger.warning(f"❌ {person_name} → REJETÉ : {reason}")
            
            return (is_valid, reason)
        
        return (False, "Erreur de validation")
        
    except Exception as e:
        logger.error(f"Erreur lors de la validation : {e}")
        return (False, f"Erreur technique : {e}")

def get_person_info_from_wikipedia(person_name: str) -> dict:
    """
    Récupère les informations d'une personne depuis Wikipedia
    """
    logger.info(f"📖 Récupération des infos pour : {person_name}")
    
    try:
        page = wikipedia.page(person_name, auto_suggest=True)
        summary = page.summary
        full_content = page.content[:3000]
        
        schema = """
        {
          "date_naissance": "Date de naissance au format YYYY-MM-DD si possible, sinon texte",
          "lieu_naissance": "Ville et pays de naissance",
          "nationalite": "Nationalité",
          "genre": "homme ou femme",
          "statut": "Profession ou fonction principale actuelle",
          "bio": "Résumé biographique en 2-3 phrases maximum",
          "formation": "Liste des écoles, universités, diplômes - format: liste de textes courts",
          "carriere": "Liste des principales fonctions, postes, mandats - format: liste de textes courts",
          "distinctions": "Liste des distinctions, prix, décorations - format: liste de textes",
          "famille": "Noms des membres de la famille mentionnés (conjoint, enfants, parents) - format: liste de noms complets",
          "relations_professionnelles": "Noms des collaborateurs, mentors, relations professionnelles importantes - format: liste de noms complets"
        }
        """
        
        extracted_data = llm.extract_yaml_data(full_content, schema)
        
        for key in ['formation', 'carriere', 'distinctions', 'famille']:
            if key not in extracted_data or extracted_data[key] is None:
                extracted_data[key] = []
            elif isinstance(extracted_data[key], str):
                extracted_data[key] = [item.strip() for item in extracted_data[key].split(',') if item.strip()]
        
        famille = extracted_data.get('famille', [])
        relations_pro = extracted_data.get('relations_professionnelles', [])
        
        if isinstance(famille, str):
            famille = [item.strip() for item in famille.split(',') if item.strip()]
        if isinstance(relations_pro, str):
            relations_pro = [item.strip() for item in relations_pro.split(',') if item.strip()]
        
        all_relations = list(set(famille + relations_pro))
        
        extracted_data['liens'] = all_relations[:15]
        extracted_data['famille'] = famille[:10] if famille else []
        extracted_data['wikipedia_url'] = page.url
        
        return extracted_data
        
    except wikipedia.exceptions.DisambiguationError as e:
        logger.warning(f"⚠️  Ambiguïté pour {person_name}. Tentative avec : {e.options[0]}")
        try:
            page = wikipedia.page(e.options[0])
            full_content = page.content[:3000]
            
            schema = """
            {
              "date_naissance": "Date de naissance",
              "lieu_naissance": "Lieu de naissance",
              "nationalite": "Nationalité",
              "genre": "Genre",
              "statut": "Statut professionnel",
              "bio": "Biographie courte",
              "formation": "Formation (liste)",
              "carriere": "Carrière (liste)",
              "distinctions": "Distinctions (liste)",
              "famille": "Famille (liste de noms)",
              "relations_professionnelles": "Relations (liste de noms)"
            }
            """
            
            extracted_data = llm.extract_yaml_data(full_content, schema)
            
            for key in ['formation', 'carriere', 'distinctions', 'famille']:
                if key not in extracted_data or extracted_data[key] is None:
                    extracted_data[key] = []
                elif isinstance(extracted_data[key], str):
                    extracted_data[key] = [item.strip() for item in extracted_data[key].split(',') if item.strip()]
            
            famille = extracted_data.get('famille', [])
            relations_pro = extracted_data.get('relations_professionnelles', [])
            
            if isinstance(famille, str):
                famille = [item.strip() for item in famille.split(',') if item.strip()]
            if isinstance(relations_pro, str):
                relations_pro = [item.strip() for item in relations_pro.split(',') if item.strip()]
            
            all_relations = list(set(famille + relations_pro))
            
            extracted_data['liens'] = all_relations[:15]
            extracted_data['famille'] = famille[:10] if famille else []
            extracted_data['wikipedia_url'] = page.url
            
            return extracted_data
        except:
            return None
    except Exception as e:
        logger.error(f"Erreur pour {person_name} : {e}")
        return None

def create_person_file(person_name: str, person_data: dict, organizations: list = [], found_via: str = "", depth: int = 0):
    """
    Crée un fichier Markdown pour une personne dans le dossier personnes/
    """
    personnes_folder = Path("personnes")
    personnes_folder.mkdir(exist_ok=True)
    
    # Regex corrigé sur une seule ligne
    safe_filename = re.sub(r'[^\w\s-]', '', person_name).strip().replace(' ', '-')
    file_path = personnes_folder / f"{safe_filename}.md"
    
    if file_path.exists():
        logger.info(f"ℹ️  {person_name} existe déjà, ignoré")
        return
    
    liens = person_data.get('liens', [])
    famille = person_data.get('famille', [])
    
    # Section Organisations
    org_text = ""
    if organizations and len(organizations) > 0:
        org_text = "\n## Organisations\n\n"
        for org in organizations:
            org_text += f"- [[{org}]]\n"
    
    # Section Relations
    relations_text = ""
    if liens and len(liens) > 0:
        relations_text = "\n## Relations et Réseaux\n\n"
        for related in liens:
            if related and len(related.strip()) > 2:
                relations_text += f"- [[{related}]]\n"
    
    # Section Famille
    famille_text = ""
    if famille and len(famille) > 0:
        famille_text = "\n## Famille\n\n"
        for member in famille:
            if member and len(member.strip()) > 2:
                famille_text += f"- [[{member}]]\n"
    
    # Section Découverte (si trouvé via exploration)
    discovery_text = ""
    if depth > 0 and found_via:
        discovery_text = f"\n> 🔍 Trouvé via **[[{found_via}]]** (profondeur {depth})\n"
    
    bio = person_data.get('bio', '')
    wiki_url = person_data.get('wikipedia_url', '')
    
    content = f"""{bio}
{discovery_text}
{org_text}
{famille_text}
{relations_text}

---

**Source** : [Wikipedia]({wiki_url})
"""
    
    # Ajouter les organisations aux affiliations
    affiliations = person_data.get('affiliations', [])
    if organizations:
        affiliations.extend(organizations)
    
    metadata = {
        'type': 'personne',
        'nom_complet': person_name,
        'nom_naissance': person_data.get('nom_naissance', ''),
        'prenoms': person_name.split()[0] if ' ' in person_name else person_name,
        'date_naissance': person_data.get('date_naissance', ''),
        'lieu_naissance': person_data.get('lieu_naissance', ''),
        'nationalite': person_data.get('nationalite', ''),
        'genre': person_data.get('genre', ''),
        'statut': person_data.get('statut', ''),
        'bio': bio,
        'formation': person_data.get('formation', []),
        'carriere': person_data.get('carriere', []),
        'affiliations': affiliations,
        'distinctions': person_data.get('distinctions', []),
        'famille': famille,
        'liens': liens,
        'presse': [],
        'sources': [wiki_url] if wiki_url else [],
        'statut_note': 'a_valider',
        'tags': ['elite', 'wikipedia', f'profondeur-{depth}'],
        'date_creation_note': datetime.now().strftime('%Y-%m-%d'),
        'found_via': found_via,
        'search_depth': depth
    }
    
    post = frontmatter.Post(content, **metadata)
    
    with open(file_path, 'wb') as f:
        frontmatter.dump(post, f)
    
    logger.info(f"✅ Fichier créé : {file_path}")

def main(query: str = None):
    """
    Script principal avec validation et exploration récursive
    """
    global VISITED_PEOPLE, ALL_FOUND_PEOPLE
    
    # Réinitialiser les variables globales
    VISITED_PEOPLE = set()
    ALL_FOUND_PEOPLE = []
    
    print("\n" + "="*60)
    print("🔍 AJOUT DE PERSONNES VIA WIKIPEDIA (Exploration 3 degrés)")
    print("="*60)
    
    if not query:
        print("\nExemples de requêtes :")
        print("  - les présidents de la 5e république")
        print("  - les ministres de l'économie français")
        print("  - les PDG du CAC 40")
        print("  - Jeffrey Epstein")
        print("  - dirigeant du Groupe EBRA")
        print("="*60)
        
        query = input("\n👤 Qui voulez-vous chercher ? : ").strip()
    
    if not query:
        logger.error("❌ Requête vide, abandon")
        return
    
    logger.info(f"🚀 Lancement de la recherche : '{query}'")
    
    # Extraction du sujet principal et des organisations
    main_subject, subject_type = extract_main_subject_from_query(query)
    organizations = extract_organization_from_query(query)
    
    if main_subject:
        logger.info(f"🎯 Sujet principal : {main_subject} (type: {subject_type})")
    if organizations:
        logger.info(f"🏢 Organisations détectées : {organizations}")
    
    # Recherche récursive (3 degrés)
    print(f"\n🌳 Exploration en profondeur (3 degrés)...")
    search_people_on_wikipedia_recursive(query, current_depth=0, max_depth=3)
    
    if not ALL_FOUND_PEOPLE or len(ALL_FOUND_PEOPLE) == 0:
        logger.warning("❌ Aucune personne trouvée pour cette requête")
        return
    
    print(f"\n📋 {len(ALL_FOUND_PEOPLE)} personnes trouvées :")
    for i, person_data in enumerate(ALL_FOUND_PEOPLE, 1):
        print(f"   {i}. {person_data['name']} (profondeur {person_data['depth']}, via: {person_data['found_via']})")
    
    # Validation et traitement
    added_count = 0
    validated_people = []
    rejected_people = []
    
    for person_data in ALL_FOUND_PEOPLE:
        person_name = person_data['name']
        depth = person_data['depth']
        found_via = person_data['found_via']
        
        logger.info(f"\n{'='*50}")
        logger.info(f"Traitement de : {person_name} (profondeur {depth})")
        
        # VALIDATION
        is_valid, reason = validate_person_relevance(person_name, query, depth)
        
        if not is_valid:
            rejected_people.append((person_name, reason, depth))
            logger.warning(f"⚠️  {person_name} rejeté : {reason}")
            continue
        
        # Si validé, récupération des données
        wiki_data = get_person_info_from_wikipedia(person_name)
        
        if wiki_data:
            create_person_file(person_name, wiki_data, organizations, found_via, depth)
            validated_people.append((person_name, depth, found_via))
            added_count += 1
        else:
            rejected_people.append((person_name, "Impossible de récupérer les données Wikipedia", depth))
            logger.warning(f"⚠️  Impossible de récupérer les données pour {person_name}")
    
    # RÉSUMÉ FINAL
    print("\n" + "="*60)
    print("📊 RÉSUMÉ DE LA VALIDATION")
    print("="*60)
    
    if validated_people:
        print(f"\n✅ Personnes VALIDÉES (ajoutées) : {len(validated_people)}")
        for i, (name, depth, found_via) in enumerate(validated_people, 1):
            print(f"   {i}. {name} (profondeur {depth}, via: {found_via})")
    
    if rejected_people:
        print(f"\n❌ Personnes REJETÉES : {len(rejected_people)}")
        for i, (name, reason, depth) in enumerate(rejected_people, 1):
            print(f"   {i}. {name} (profondeur {depth}) → {reason}")
    
    print("\n" + "="*60)
    print(f"🎉 RÉSULTAT FINAL : {added_count} fiches créées, {len(rejected_people)} rejetées")
    print(f"🌳 Exploration sur {max([p['depth'] for p in ALL_FOUND_PEOPLE]) + 1} niveaux")
    print("="*60)
    
    # Commit Git
    if added_count > 0:
        commit_msg = f"feat: ajout de {added_count} personnes via Wikipedia (3 degrés) - {query}"
        git.commit_changes(commit_msg)
        logger.info("✅ Changements committés")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query_arg = ' '.join(sys.argv[1:])
        main(query_arg)
    else:
        main()

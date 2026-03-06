# 📚 French Connexion - Documentation Technique Complète

## 🎯 Vue d'ensemble du projet

**French Connexion** est une base de données collaborative et intelligente destinée à cartographier les élites françaises (personnalités, institutions, entreprises, médias, think tanks). Le système utilise l'Intelligence Artificielle (Mistral AI) pour automatiser la classification, l'enrichissement et l'interconnexion des fiches d'entités.

### Technologies utilisées
- **Backend**: Python 3.x
- **IA**: Mistral AI (API)
- **NLP**: Spacy (reconnaissance d'entités nommées)
- **Frontend**: Vue.js 3 + Tailwind CSS
- **Format de données**: Markdown avec YAML frontmatter
- **Versionning**: Git avec commits automatiques

---

## 📂 Architecture du Projet

```
french-connexion/
├── config/
│   └── config.yaml              # Configuration centrale (types, IA)
├── src/
│   ├── utils/                   # Modules utilitaires réutilisables
│   │   ├── logger.py           # Système de logging
│   │   ├── llm_client.py       # Client API Mistral AI
│   │   ├── git_handler.py      # Gestion automatique Git
│   │   ├── diagnostic_mistral.py   # Outil de diagnostic SDK
│   │   └── update_imports.py   # Migration automatique de code
│   └── templates/               # Templates YAML pour chaque type d'entité
│       ├── personne.yaml
│       └── organisation.yaml
├── scripts/                     # Scripts d'automatisation
│   ├── 01_classify_and_structure.py    # Classification & structuration IA
│   ├── 02_link_entities.py             # Création de liens wiki [[...]]
│   ├── 03_enrich_wikipedia.py          # Enrichissement via Wikipedia
│   └── 05_repo_optimizer.py            # Standardisation des métadonnées
├── personnes/                   # Dossier des fiches personnes
├── institutions/                # Dossier des fiches institutions
├── companies/                   # Dossier des fiches entreprises
├── medias/                      # Dossier des fiches médias
├── think tanks/                 # Dossier des fiches think tanks
├── écoles/                      # Dossier des fiches écoles
├── logs/                        # Logs système
├── index.html                   # Interface web interactive
└── requirements.txt             # Dépendances Python
```

---

## 🔧 Fichiers de Configuration

### `config/config.yaml`

**Rôle** : Configuration centrale du projet définissant les comportements de tous les scripts.

**Structure** :
```yaml
entity_types:          # Définit les types d'entités et leurs dossiers
  Personne:
    folder: "personnes"
    template: "src/templates/personne.yaml"
  Institution:
    folder: "institutions"
    template: "src/templates/organisation.yaml"

llm:                   # Configuration de l'IA
  model: "open-mistral-nemo"
  temperature: 0.2
  max_tokens: 2048

linking:               # Paramètres de création de liens
  min_confidence_score: 0.8
  ignore_patterns: ["le", "la", ...]
```

**⚠️ Conséquences des modifications** :
- Modifier un `folder` déplacera les fichiers lors du prochain run de `01_classify_and_structure.py`
- Changer le modèle IA affectera la qualité et le coût des analyses

---

## 🛠️ Modules Utilitaires (`src/utils/`)

### 1. `logger.py`

**Rôle** : Configure le système de logging centralisé pour tracer toutes les opérations.

**Fonctions principales** :
```python
def setup_logger(name="french_connection", log_file="logs/system.log"):
    """
    Crée un logger qui écrit simultanément dans :
    - La console (stdout) pour le suivi en temps réel
    - Un fichier de log (logs/system.log) pour l'historique
    """
```

**Utilisation** :
```python
from src.utils.logger import setup_logger
logger = setup_logger()
logger.info("Message d'information")
logger.error("Message d'erreur")
```

**⚠️ Conséquences** :
- Tous les scripts dépendent de ce module pour le logging
- Les logs permettent de débugger les erreurs d'enrichissement IA
- Modifier le format des logs affectera la lisibilité des traces

---

### 2. `llm_client.py` ⭐

**Rôle** : Client centralisé pour interagir avec l'API Mistral AI. C'est le **cœur de l'intelligence** du système.

**Classe principale** :
```python
class MistralClient:
    def __init__(self):
        # Initialise le client Mistral AI avec la clé API
        # Récupère MISTRAL_API_KEY depuis les variables d'environnement
```

**Méthodes critiques** :

#### 2.1 `intelligent_restructure(content, title, template_path)`
**Objectif** : Analyser un document brut et déterminer automatiquement son type + extraire des métadonnées.

**Prompt système** :
```
"Tu es un assistant expert en analyse de documents.
Renvoie UNIQUEMENT un objet JSON avec :
- type : Personne, Institution, Evenement, Concept
- summary : Résumé en 2 phrases
- keywords : 5 mots-clés pertinents"
```

**Retour** : Dict JSON
```python
{
  "type": "Personne",
  "summary": "Emmanuel Macron est...",
  "keywords": ["politique", "président", "ENA", ...]
}
```

**⚠️ Conséquences** :
- Utilisé par `01_classify_and_structure.py` pour le tri automatique
- Si l'IA se trompe de type, la fiche sera classée dans le mauvais dossier
- Le coût API dépend du nombre de fiches à analyser

---

#### 2.2 `extract_yaml_data(text, schema_description)`
**Objectif** : Extraire des données **structurées précises** depuis un texte brut (ex: Wikipedia).

**Prompt système** :
```
"Tu es un extracteur de données métier.
Consignes STRICTES :
1. Renvoie UNIQUEMENT un JSON valide
2. Ne rédige aucune phrase
3. N'inclus pas de champs si l'info n'existe pas
4. Respecte ce schéma : {schema_description}"
```

**Exemple de schéma pour une Personne** :
```json
{
  "birth_date": "Date de naissance (YYYY-MM-DD)",
  "birth_place": "Lieu de naissance",
  "nationality": "Nationalité",
  "occupation": "Profession",
  "education": "Formation (alma_mater)",
  "website": "Site officiel"
}
```

**Retour** : Dict JSON avec données extraites
```python
{
  "birth_date": "1977-12-21",
  "birth_place": "Amiens, France",
  "nationality": "Française",
  "occupation": "Président de la République",
  "education": "Sciences Po, ENA",
  "website": "https://..."
}
```

**⚠️ Conséquences** :
- Utilisé par `03_enrich_wikipedia.py` pour enrichir les fiches
- La qualité du schéma détermine la qualité des données extraites
- Si le schéma est incomplet, des informations importantes seront perdues

---

### 3. `git_handler.py`

**Rôle** : Automatiser les commits Git après chaque modification de fichiers.

**Fonctions principales** :

```python
class GitHandler:
    def commit_changes(self, message):
        """
        1. git add .
        2. Vérifie s'il y a des changements (git diff --cached --quiet)
        3. Si oui : git commit -m "message"
        """
    
    def create_backup_tag(self):
        """
        Crée un tag horodaté avant modifications lourdes
        Exemple: backup_20260212_143052
        """
```

**⚠️ Conséquences** :
- Chaque script automatique crée un commit → historique traçable
- Les tags de backup permettent de restaurer avant une erreur massive
- Si Git n'est pas configuré (user.name/email), les commits échouent

---

### 4. `diagnostic_mistral.py`

**Rôle** : Script de diagnostic pour vérifier l'installation du SDK Mistral AI.

**Tests effectués** :
1. ✅ Package `mistralai` installé ?
2. ✅ Version du SDK (v0.x ou v1.0+) ?
3. ✅ API v1.0+ disponible ? (recommandée)
4. ✅ API v0.x disponible ? (obsolète)

**Usage** :
```bash
python src/utils/diagnostic_mistral.py
```

**⚠️ Conséquences** :
- Identifier les problèmes de compatibilité avant de lancer les scripts
- Éviter les erreurs d'import au milieu d'un traitement

---

### 5. `update_imports.py`

**Rôle** : Script de migration automatique pour renommer les imports de classes.

**Exemple** : Renommer `MistralClient` en `MistralAIClient` dans tous les fichiers Python.

**Patterns détectés** :
```python
# Pattern 1
from src.utils.llm_client import MistralClient
→ from src.utils.llm_client import MistralAIClient

# Pattern 2
MistralClient()
→ MistralAIClient()
```

**⚠️ Conséquences** :
- Utile pour refactoring à grande échelle
- **Attention** : modifie tous les fichiers Python du projet
- Exclut automatiquement les dossiers venv, .git, __pycache__

---

## 📜 Scripts d'Automatisation (`scripts/`)

### Script 1 : `01_classify_and_structure.py` 🔥

**Rôle** : **Classification et structuration intelligente** des documents bruts.

**Workflow** :
```
1. Lire un fichier Markdown brut (sans type défini)
2. Appeler llm.intelligent_restructure(contenu)
3. L'IA détermine le type (Personne / Institution / Entreprise / etc.)
4. L'IA extrait un résumé + mots-clés
5. Déplacer le fichier dans le bon dossier (personnes/, institutions/, etc.)
6. Écrire les métadonnées en frontmatter YAML
7. Commit Git automatique
```

**Code clé** :
```python
def process_file(file_path):
    post = frontmatter.load(file_path)
    
    # Si déjà classé, on ignore
    if 'type' in post.metadata:
        logger.info(f"Déjà classé. Ignoré.")
        return
    
    # Analyse IA
    new_metadata = llm.intelligent_restructure(post.content, title, template)
    entity_type = new_metadata.get('type', 'Institution')
    
    # Déplacement dans le bon dossier
    target_folder = Path(CONFIG['entity_types'][entity_type]['folder'])
    shutil.move(file_path, target_folder / file_path.name)
    
    # Écriture du frontmatter
    new_post = frontmatter.Post(content, **new_metadata)
    with open(new_path, 'wb') as f:
        frontmatter.dump(new_post, f)
```

**⚠️ Conséquences critiques** :
- **Ce script modifie la structure du repository** (déplace les fichiers)
- Si l'IA se trompe, une fiche peut être mal classée
- Toujours vérifier les logs après exécution
- **Mode binaire ('wb')** pour éviter les erreurs d'encodage

---

### Script 2 : `02_link_entities.py`

**Rôle** : **Génération automatique de liens wiki** `[[nom]]` entre les entités.

**Workflow** :
```
1. Construire un index de toutes les entités (nom → chemin fichier)
2. Pour chaque fichier Markdown :
   a. Analyser le texte avec Spacy NER (reconnaissance d'entités nommées)
   b. Pour chaque personne/organisation détectée :
      - Vérifier si elle existe dans l'index
      - Remplacer "Emmanuel Macron" par "[[Emmanuel Macron]]"
3. Commit Git
```

**Code clé** :
```python
def build_entity_index():
    """Crée un index : {"emmanuel_macron": "personnes/Emmanuel_Macron.md"}"""
    for f in md_files:
        post = frontmatter.load(f)
        name = post.get('nom_complet', f.stem)
        norm_name = name.lower().replace(" ", "_")
        ENTITY_INDEX[norm_name] = str(f)

def link_document(file_path):
    doc = nlp(content)  # Analyse Spacy
    
    for ent in doc.ents:
        if ent.label_ in ["PERSON", "ORG"]:
            if ent.text in ENTITY_INDEX:
                content = content.replace(ent.text, f"[[{ent.text}]]", 1)
```

**⚠️ Conséquences** :
- Nécessite le modèle Spacy français : `python -m spacy download fr_core_news_lg`
- Les liens créés sont **compatibles Obsidian**
- Peut créer des faux positifs (ex: "Le Monde" lien vers la fiche du journal)
- **Attention** : remplace seulement la première occurrence pour éviter la saturation

---

### Script 3 : `03_enrich_wikipedia.py` ⭐

**Rôle** : **Enrichissement automatique** des fiches via Wikipedia + extraction IA.

**Workflow** :
```
1. Pour chaque fiche existante :
2. Récupérer le résumé Wikipedia (langue française)
3. Définir un schéma d'extraction selon le type (Personne / Institution)
4. Appeler llm.extract_yaml_data(résumé_wikipedia, schéma)
5. L'IA extrait des données structurées (date de naissance, siège social, etc.)
6. Fusionner les nouvelles données avec les métadonnées existantes
7. Marquer la fiche comme enrichie (wikipedia_enriched: true)
8. Commit Git
```

**Schémas d'extraction** :
```python
def get_schema_for_type(entity_type):
    if entity_type == "Personne":
        return {
          "birth_date": "Date de naissance (YYYY-MM-DD)",
          "birth_place": "Lieu de naissance",
          "nationality": "Nationalité",
          "occupation": "Profession",
          "education": "Formation",
          "website": "Site officiel"
        }
    elif entity_type == "Institution":
        return {
          "founded": "Date de création",
          "headquarters": "Siège social",
          "leader": "Dirigeant actuel",
          "industry": "Secteur d'activité",
          "website": "Site officiel"
        }
```

**Code clé** :
```python
# Récupération Wikipedia
wiki_page = wikipedia.page(title, auto_suggest=False)
wiki_summary = wiki_page.summary

# Extraction IA
schema = get_schema_for_type(entity_type)
extracted_data = llm.extract_yaml_data(wiki_summary, schema)

# Fusion
metadata.update(extracted_data)
metadata['wikipedia_enriched'] = True
```

**⚠️ Conséquences** :
- **Coût API** : 1 appel Mistral par fiche enrichie
- Si le titre ne correspond pas exactement à Wikipedia, la recherche échoue
- Gestion des pages ambiguës : essaie la première suggestion
- Les données extraites peuvent écraser les données manuelles existantes

---

### Script 5 : `05_repo_optimizer.py`

**Rôle** : **Analyse et standardisation** des champs de métadonnées.

**Workflow** :
```
1. Analyser toutes les fiches d'un type (ex: Personne)
2. Lister tous les champs utilisés + fréquence
3. Détecter les doublons/incohérences ("education" vs "formation")
4. Appliquer des règles de renommage automatiques
5. Commit Git
```

**Code clé** :
```python
def analyze_field_consistency(entity_type):
    """Analyse les champs utilisés"""
    field_usage = Counter()
    
    for f in md_files:
        post = frontmatter.load(f)
        for key in post.metadata.keys():
            field_usage[key] += 1
    
    # Résultat : {"nom_complet": 45, "education": 38, "formation": 12, ...}

def auto_standardize(entity_type, mapping_rules):
    """Applique des règles : {"education": "formation"}"""
    for f in md_files:
        if "education" in metadata:
            metadata["formation"] = metadata.pop("education")
```

**⚠️ Conséquences** :
- **Attention** : modifie en masse les métadonnées
- Toujours créer un backup tag avant : `git.create_backup_tag()`
- Les règles de mapping doivent être testées sur un échantillon
- Utile pour harmoniser après imports en masse

---

### Script 6 : `06_add_people_from_wikipedia.py` ⭐🆕

**Rôle** : **Exploration intelligente de réseaux** avec IA avancée et limites configurables.

**VERSION ENHANCED** avec nouveautés critiques :

**Workflow optimisé** :
```
1. 📋 DEEP QUERY ANALYSIS
   - Mistral analyse la requête en profondeur
   - Génère un plan de recherche avec priorités
   - Estime le nombre d'entités et le temps nécessaire

2. 🎯 PRÉ-VALIDATION INTELLIGENTE
   - Avant chaque appel Wikipedia (coûteux), Mistral évalue la pertinence
   - Score 0-100 : seules les entités ≥ 70 sont explorées
   - Économie massive d'API calls Wikipedia

3. 🌳 EXPLORATION MULTI-NIVEAUX
   - Niveau 0 : Entités principales (sans pré-validation)
   - Niveau 1-2 : Relations (avec pré-validation stricte)
   - Limites strictes pour éviter timeouts GitHub Actions

4. ✅ VALIDATION FINALE
   - Validation de la pertinence de chaque personne trouvée
   - Création des fiches Obsidian avec liens [[...]]
   - Rapport détaillé avec statistiques

5. 🛡️ GRACEFUL SHUTDOWN
   - Arrêt automatique si limite atteinte (entités/temps/Wikipedia)
   - Sauvegarde des résultats partiels
   - Pas de timeout brutal
```

**Paramètres configurables** :
```python
# Détection automatique de l'environnement
IS_GITHUB_ACTION = os.getenv('GITHUB_ACTIONS') == 'true'

# Limites par défaut
MAX_ENTITIES_PER_RUN = 15 (GH Actions) ou 50 (Local)
MAX_WIKIPEDIA_CALLS = 20 (GH Actions) ou 100 (Local)  
TIME_LIMIT_SECONDS = 300s (GH Actions) ou Aucune (Local)
MIN_PRIORITY_SCORE = 70  # Score minimum pour exploration

# Override via variables d'environnement
MAX_ENTITIES=30 TIME_LIMIT=600 python scripts/06_add_people_from_wikipedia.py "Le Siècle"
```

**Nouvelles fonctions critiques** :

1. **`mistral_analyze_query_deeply(query)`**
   - Analyse approfondie de la requête
   - Retour : Plan avec priorités, estimation, complexité
   ```json
   {
     "query_analysis": "Club d'influence français...",
     "primary_targets": ["Henri de Castries", "Anne Lauvergeon"],
     "estimated_total_entities": 30,
     "recommended_depth": 2,
     "focus_areas": ["réseaux d'influence", "élites"],
     "quality_threshold": 75
   }
   ```

2. **`mistral_score_entity_relevance(entity, query, plan)`**
   - Pré-validation AVANT appel Wikipedia
   - Retour : (score 0-100, raisonnement)
   - Économie massive : skip les entités < 70
   ```python
   score, reason = mistral_score_entity_relevance("Jean Dupont", "Le Siècle", plan)
   # (35, "Nom commun, pas de lien évident avec Le Siècle")
   # → SKIP, pas d'appel Wikipedia
   ```

3. **`generate_research_plan(query)`**
   - Combine deep analysis avec stratégie d'exécution
   - Sépare cibles primaires (priorité ≥ 85) des secondaires
   - Retour : Plan structuré prêt à l'emploi

**Statistiques trackées** :
```python
EXPLORATION_STATS = {
    'pre_validations_performed': 45,      # Pré-validations effectuées
    'pre_validations_passed': 18,         # Score ≥ 70 (40%)
    'pre_validations_rejected': 27,       # Score < 70 (60% économisés!)
    'wikipedia_limit_reached': 0,         # Limite atteinte ou non
    'mistral_calls': 68,                  # Appels Mistral total
    'factcheck_success': 18,              # Wikipedia trouvés
    # ... autres stats existantes
}
```

**Flux de contrôle avec limites** :
```python
for entity in prioritized_entities:
    # Vérifications AVANT traitement
    if processed_count >= MAX_ENTITIES_PER_RUN:
        print(f"⚠️ Limite d'entités atteinte ({MAX_ENTITIES_PER_RUN})")
        break
    
    if time.time() - start_time > TIME_LIMIT_SECONDS:
        print(f"⚠️ Limite de temps atteinte ({TIME_LIMIT_SECONDS}s)")
        break
    
    # Pré-validation (nouveau, économie d'API)
    if ENABLE_PRE_VALIDATION and depth > 0:
        score, reason = mistral_score_entity_relevance(entity, query, plan)
        if score < MIN_PRIORITY_SCORE:
            print(f"⏭️ {entity} ignoré (score: {score}/100)")
            continue  # Pas d'appel Wikipedia !
    
    # Wikipedia (coûteux, seulement si pertinent)
    wiki_data = wikipedia_factcheck_person_rigorous(entity)
    if wiki_data:
        create_person_file(entity, wiki_data)
        processed_count += 1
```

**Affichage enrichi** :
```
🧠 ŒIL DE DIEU - Construction de réseau de pouvoir
==============================================================

⚙️ Paramètres :
  - Environnement : GitHub Actions
  - Limite d'entités : 15
  - Limite Wikipedia : 20
  - Limite de temps : 300s (5min)
  - Score minimum : 70/100
  - Pré-validation : ACTIVÉE

📋 Phase -1 : Génération du plan de recherche...
✅ Plan généré : 25 entités estimées, focus: [élites, influence]

🎯 Phase 0 : Analyse de la requête...
✅ 12 personnes identifiées

🌳 Phase 1 : Exploration intelligente...
   🔄 Traitement 1/12: Henri de Castries... (temps: 5s, entités: 0/15)
      ✅ Score: 95/100 - Wikipedia lookup...
   🔄 Traitement 2/12: Jean Dupont... (temps: 8s, entités: 1/15)
      ⏭️ Ignoré (score: 35/100)
   ...
   ⚠️ Limite d'entités atteinte (15), arrêt

📊 STATISTIQUES FINALES :
   🎯 Pré-validations :
      - Effectuées : 45
      - Acceptées : 18 (40%)
      - Rejetées : 27 (60%)
      - Wikipedia calls économisés : 27

   📊 Limites :
      - Entités traitées : 15/15 (100%)
      - Appels Wikipedia : 18/20 (90%)
      - Temps utilisé : 285s / 300s (95%)
```

**⚠️ Conséquences critiques** :
- **Coût API optimisé** : Pré-validation économise ~60% d'appels Wikipedia
- **Pas de timeout GitHub Actions** : Limites strictes garantissent l'arrêt avant 6min
- **Résultats partiels sauvegardés** : Arrêt gracieux = données préservées
- **Prioritisation intelligente** : Traite d'abord les entités les plus pertinentes
- **Statistiques détaillées** : Permet d'optimiser les seuils (MIN_PRIORITY_SCORE)

**Cas d'usage** :
```bash
# Mode GitHub Actions (automatique via workflow)
# Limites : 15 entités, 20 Wikipedia, 5min

# Mode local développement (limites étendues)
python scripts/06_add_people_from_wikipedia.py "Le Siècle"
# Limites : 50 entités, 100 Wikipedia, aucune limite temps

# Mode custom (override)
MAX_ENTITIES=10 MIN_SCORE=80 python scripts/06_add_people_from_wikipedia.py "Groupe Bilderberg"
```

**Variables d'environnement supportées** :
- `GITHUB_ACTIONS` : Détecté automatiquement (ajuste les limites)
- `MAX_ENTITIES` : Override de MAX_ENTITIES_PER_RUN
- `MAX_WIKI_CALLS` : Override de MAX_WIKIPEDIA_CALLS
- `TIME_LIMIT` : Override de TIME_LIMIT_SECONDS (secondes)
- `MISTRAL_API_KEY` : Clé API Mistral (obligatoire)
- `MISTRAL_MODEL` : Modèle Mistral à utiliser (optionnel)

---

## 📄 Templates YAML

### `src/templates/personne.yaml`

**Rôle** : Schéma de référence pour les fiches de type "Personne".

**Champs principaux** :
```yaml
type: personne
nom_complet: ""           # Nom complet officiel
nom_naissance: ""         # Nom de naissance (si différent)
date_naissance: ""        # Format YYYY-MM-DD
lieu_naissance: ""        # Ville, Pays
nationalite: ""
formation: []             # Liste des écoles/diplômes
carriere: []              # Mandats professionnels chronologiques
affiliations: []          # Clubs, réseaux (ex: Le Siècle, Trilateral)
distinctions: []          # Légion d'honneur, etc.
famille: []               # Liens familiaux vers d'autres fiches
liens: []                 # Backlinks automatiques
sources: []               # URLs de sources
tags: ["elite"]
```

**⚠️ Conséquences** :
- Ce template guide l'extraction IA dans `03_enrich_wikipedia.py`
- Ajouter un champ ici ne l'activera pas automatiquement (modifier aussi le schéma IA)
- Les listes (`[]`) permettent des valeurs multiples

---

### `src/templates/organisation.yaml`

**Rôle** : Schéma pour Institutions, Entreprises, Médias, Think Tanks, etc.

**Champs principaux** :
```yaml
type: organisation
nom: ""
nom_court: ""             # Acronyme (ex: "ENA")
type_org: ""              # entreprise | institution | media | think_tank | ecole
secteur: ""               # Secteur d'activité (pour entreprises)
siege: ""                 # Ville, Pays
date_creation: ""
dirigeants: []            # Liste des personnes clés
membres: []               # Liste des membres (pour clubs)
affiliations: []          # Fédérations, groupes d'appartenance
sites_web: []
sources: []
```

**⚠️ Conséquences** :
- Un seul template pour tous les types d'organisations (mutualisation)
- Le champ `type_org` permet de différencier finement
- Les listes `dirigeants` et `membres` devraient contenir des liens wiki `[[...]]`

---

## 🌐 Interface Web (`index.html`)

**Rôle** : Application web **Vue.js 3** monopage pour explorer les fiches.

**Technologies** :
- **Vue.js 3** : Réactivité et composants
- **Tailwind CSS** : Design moderne
- **Marked.js** : Rendu Markdown → HTML
- **GitHub API** : Récupération directe des fichiers (pas de backend)

**Architecture** :
```javascript
1. Au chargement :
   - Fetch de tous les dossiers en parallèle (API GitHub)
   - Parse du frontmatter YAML de chaque fiche
   - Extraction des connexions [[...]]
   - Stockage en mémoire

2. Fonctionnalités :
   - Recherche full-text (nom, résumé, mots-clés)
   - Filtrage par type (Personne, Institution, etc.)
   - Tri (alphabétique, par date, par type)
   - Affichage métadonnées/connexions toggleable
   - Modal de lecture complète
   - Navigation par connexions cliquables
```

**Fonctions critiques** :

```javascript
// Parse le frontmatter YAML (résistant aux erreurs)
const parseFrontmatter = (content) => {
  const parts = content.split('---');
  // Gère les tableaux YAML (keywords, carriere, etc.)
  // Gère les valeurs nulles
  // Retire les guillemets
}

// Extrait les liens [[nom]]
const extractConnections = (content) => {
  const regex = /\[\[([^\]]+)\]\]/g;
  // Retourne la liste des noms référencés
}

// Navigation entre fiches
const searchAndOpen = (name) => {
  const file = allFiles.value.find(f => f.metadata.title === name);
  if (file) openFile(file);
}
```

**⚠️ Conséquences** :
- **Pas de backend** : tout se fait côté client (GitHub Pages compatible)
- Limite de 60 requêtes/heure (API GitHub non authentifiée)
- Les dossiers listés sont **hardcodés** (ligne 275)
- Le parser YAML est simplifié (peut échouer sur YAML complexe)

---

## 🚀 Guide d'utilisation pour l'IA

### Avant toute modification :

1. **Lire ce README** pour comprendre les dépendances
2. **Vérifier les logs** (`logs/system.log`) pour identifier les erreurs
3. **Créer un backup tag** : `GitHandler().create_backup_tag()`
4. **Tester sur un échantillon** avant modification en masse

### Workflow recommandé pour enrichir le repository :

```bash
# 1. Installer les dépendances
pip install -r requirements.txt
python -m spacy download fr_core_news_lg

# 2. Configurer l'API Mistral
export MISTRAL_API_KEY="votre_clé"

# 3. Diagnostic (optionnel)
python src/utils/diagnostic_mistral.py

# 4. Classification des documents bruts
python scripts/01_classify_and_structure.py

# 5. Enrichissement Wikipedia
python scripts/03_enrich_wikipedia.py

# 6. Création des liens
python scripts/02_link_entities.py

# 7. Vérifier le résultat dans l'interface web
# Ouvrir index.html dans un navigateur
```

### Conséquences de chaque action :

| Action | Fichiers modifiés | Réversible ? | Coût API |
|--------|------------------|--------------|----------|
| `01_classify_and_structure.py` | Tous les `.md` non classés | ⚠️ Oui (via Git) | 1 appel/fichier |
| `02_link_entities.py` | Tous les `.md` | ✅ Oui | Gratuit (Spacy local) |
| `03_enrich_wikipedia.py` | Fiches non enrichies | ✅ Oui | 1 appel/fiche |
| `05_repo_optimizer.py` | ⚠️ Toutes les fiches d'un type | ⚠️ Backup requis | Gratuit |
| `06_add_people_from_wikipedia.py` 🆕 | Nouvelles fiches uniquement | ✅ Oui | 2-4 appels/personne (optimisé) |

**Note sur Script 06** : Grâce à la pré-validation, économise ~60% d'appels Wikipedia. Coût typique :
- 1 appel Mistral pour analyse initiale + plan
- 1 appel Mistral pour pré-validation (skip si score < 70)
- 1 appel Wikipedia (seulement si pertinent)
- 1 appel Mistral pour extraction relations
- **Total réel** : ~2 appels Mistral par personne validée vs ~4 sans pré-validation

### Points d'attention critiques :

1. **API Mistral** :
   - ⚠️ Chaque appel coûte de l'argent
   - ⚠️ Toujours vérifier les résultats avant validation
   - ✅ Les prompts sont optimisés pour éviter les hallucinations

2. **Git** :
   - ✅ Chaque script crée un commit → traçabilité totale
   - ⚠️ Les tags de backup sont **essentiels** avant optimisation massive
   - ✅ Historique complet permet de restaurer n'importe quelle version

3. **Qualité des données** :
   - ⚠️ Wikipedia peut être incomplet ou ambigu
   - ⚠️ Spacy NER a un taux d'erreur (faux positifs/négatifs)
   - ✅ Validation manuelle recommandée sur les fiches critiques

4. **Performance** :
   - ⚠️ L'interface web charge TOUS les fichiers au démarrage
   - ⚠️ Au-delà de 500 fiches, envisager une pagination
   - ✅ Le chargement parallèle optimise les performances

---

## 📊 Schéma de flux de données

```
[Documents bruts .md]
        ↓
[01_classify_and_structure.py]
        ↓ (IA : détermine type + résumé)
[Fiches classées par dossier]
        ↓
[03_enrich_wikipedia.py]
        ↓ (Wikipedia + IA : extraction données)
[Fiches enrichies (dates, lieux, etc.)]
        ↓
[02_link_entities.py]
        ↓ (Spacy NER : détection entités)
[Fiches interconnectées avec [[liens]]]
        ↓
[index.html]
        ↓ (Vue.js : interface de lecture)
[Visualisation interactive]
```

---

## 🔍 Diagnostic des erreurs courantes

### Erreur : `MISTRAL_API_KEY not found`
**Solution** :
```bash
export MISTRAL_API_KEY="sk-xxxxx"
# Ou créer un fichier .env
echo "MISTRAL_API_KEY=sk-xxxxx" > .env
```

### Erreur : `Modèle Spacy manquant`
**Solution** :
```bash
python -m spacy download fr_core_news_lg
```

### Erreur : `Git user.name not set`
**Solution** :
```bash
git config user.name "French Connexion Bot"
git config user.email "bot@french-connexion.local"
```

### Erreur : `write() argument must be str, not bytes`
**Solution** : Ouvrir le fichier en mode **binaire** (`'wb'`) lors de l'écriture frontmatter.

### Erreur : `Page Wikipedia non trouvée`
**Solution** : Vérifier que le titre de la fiche correspond exactement au titre Wikipedia (sensible à la casse).

---

## 📚 Ressources complémentaires

- **Mistral AI Docs** : https://docs.mistral.ai/
- **Spacy NER** : https://spacy.io/usage/linguistic-features#named-entities
- **Vue.js 3** : https://vuejs.org/guide/introduction.html
- **Python Frontmatter** : https://pypi.org/project/python-frontmatter/
- **Obsidian Wiki Links** : https://help.obsidian.md/Linking+notes+and+files/Internal+links

---

## 🎓 Conseils pour les IA contributeurs

1. **Toujours lire les logs** avant et après chaque script
2. **Tester sur 1-2 fiches** avant un run complet
3. **Créer des backups Git** régulièrement
4. **Documenter les modifications** dans les commits
5. **Valider manuellement** les enrichissements IA critiques
6. **Optimiser les prompts** si les résultats sont décevants
7. **Surveiller les coûts API** Mistral (dashboard)

---

**Version** : 1.0  
**Dernière mise à jour** : 2026-02-12  
**Mainteneur** : Système automatisé French Connexion

---

💡 **Pour toute question** : Consultez les logs (`logs/system.log`) et l'historique Git (`git log`).

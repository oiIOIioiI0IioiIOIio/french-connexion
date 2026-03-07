import sys
import os
import re
import yaml
import frontmatter
from pathlib import Path
from collections import Counter
import shutil
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.logger import setup_logger
from src.utils.git_handler import GitHandler

load_dotenv()

logger = setup_logger()
git = GitHandler()

with open("config/config.yaml", "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

MIN_SUMMARY_LENGTH = 10

# Build reverse mapping: folder name -> entity type
FOLDER_TO_TYPE = {}
for etype, econf in CONFIG.get('entity_types', {}).items():
    folder = econf.get('folder', '')
    if folder:
        folder_parts = Path(folder).parts
        if folder_parts:
            FOLDER_TO_TYPE[folder_parts[0]] = etype

# Content keyword patterns for fallback type detection on root-level files
_CONTENT_TYPE_RULES = [
    ("Personne", [
        r"\bne\s+le\b", r"\bnee\s+le\b", r"\bn[eé]\s+le\b", r"\bn[eé]e\s+le\b",
        r"\bhomme\s+politique\b", r"\bfemme\s+politique\b",
        r"\bjournaliste\b", r"\bphilosophe\b", r"\becrivain\b",
        r"\bministre\b", r"\bpresident\b", r"\bpresidente\b",
    ]),
    ("Entreprise", [
        r"\bfond[eé]e\s+en\b", r"\bentreprise\b", r"\bgroupe\b",
        r"\bsoci[eé]t[eé]\b", r"\bstart-?up\b", r"\bindustri\w+\b",
    ]),
    ("Ecole", [
        r"\b[eé]cole\b", r"\buniversit[eé]\b", r"\bgrande\s+[eé]cole\b",
        r"\bformation\b", r"\b[eé]tudiants?\b",
    ]),
    ("Media", [
        r"\bjournal\b", r"\bquotidien\b", r"\br[eé]daction\b",
        r"\bm[eé]dia\b", r"\bpresse\b", r"\bt[eé]l[eé]vision\b",
    ]),
    ("Fondation", [
        r"\bthink\s*tank\b", r"\bfondation\b", r"\bcercle\b",
        r"\binstitut\b", r"\bgroupe\s+de\s+r[eé]flexion\b",
    ]),
    ("Institution", [
        r"\binstitution\b", r"\bminist[eè]re\b", r"\br[eé]publique\b",
        r"\bgouvernement\b", r"\bass?embl[eé]e\b",
    ]),
]

FRENCH_STOPWORDS = {
    "les", "des", "une", "dans", "pour", "par", "sur", "avec", "aux",
    "est", "son", "ses", "qui", "que", "pas", "plus", "tout", "tous",
    "cette", "ces", "mais", "aussi", "comme", "ont", "sont", "leur",
    "elle", "ils", "elles", "nous", "vous", "lui", "entre", "bien",
    "peut", "fait", "ete", "dit", "dont", "depuis", "sans", "sous",
    "encore", "autre", "autres", "alors", "apres", "avant", "chez",
    "tres", "peu", "ainsi", "car", "donc", "puis", "quand", "deux",
    "premier", "premiere", "ancien", "ancienne", "grande", "grand",
    "petit", "petite", "nouveau", "nouvelle", "meme", "avoir", "etre",
    "faire", "dire", "voir", "pouvoir", "vouloir", "aller", "savoir",
}


def _detect_type_from_folder(file_path):
    """Return entity type based on the parent folder of the file."""
    parts = file_path.parts
    # Files at root (e.g. './example.md') have '.' as first part or only one part
    if len(parts) < 2:
        return None
    parent = parts[0]
    if parent == '.':
        return FOLDER_TO_TYPE.get(parts[1]) if len(parts) > 2 else None
    return FOLDER_TO_TYPE.get(parent)


def _detect_type_from_content(content):
    """Return entity type by matching keyword patterns in content."""
    text = content.lower()
    best_type = None
    best_score = 0
    for etype, patterns in _CONTENT_TYPE_RULES:
        score = sum(1 for p in patterns if re.search(p, text))
        if score > best_score:
            best_score = score
            best_type = etype
    return best_type if best_score > 0 else None


def detect_type(file_path, content):
    """Determine entity type from folder location, falling back to content analysis."""
    folder_type = _detect_type_from_folder(file_path)
    if folder_type:
        return folder_type
    content_type = _detect_type_from_content(content)
    if content_type:
        return content_type
    return "Institution"


def _is_meaningful_line(line):
    """Return True if a line has real prose (not just headers, links, or whitespace)."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    # Skip lines that are only markdown links or images
    link_only = re.sub(r'\[.*?\]\(.*?\)', '', stripped)
    link_only = re.sub(r'!\[.*?\]\(.*?\)', '', link_only)
    if not link_only.strip():
        return False
    return True


def generate_summary(content):
    """Extract the first 2 meaningful sentences from content, capped at 200 chars."""
    lines = content.split('\n')
    meaningful = []
    for line in lines:
        if _is_meaningful_line(line):
            meaningful.append(line.strip())
        if len(meaningful) >= 5:
            break

    text = ' '.join(meaningful)
    # Split into sentences on period, exclamation, or question mark followed by space
    sentences = re.split(r'(?<=[.!?])\s+', text)
    selected = []
    for s in sentences:
        s = s.strip()
        if s:
            selected.append(s)
        if len(selected) >= 2:
            break

    result = ' '.join(selected)
    if len(result) > 200:
        result = result[:197] + "..."
    return result


def extract_keywords(content, top_n=5):
    """Extract top N frequent meaningful words (3+ chars, no stopwords)."""
    text = content.lower()
    text = re.sub(r'[#\[\]\(\){}|*_`~><!]', ' ', text)
    text = re.sub(r'https?://\S+', '', text)
    words = re.findall(r'[a-zA-Zàâäéèêëïîôùûüÿçœæ]{3,}', text)
    filtered = [w for w in words if w not in FRENCH_STOPWORDS]
    counter = Counter(filtered)
    return [word for word, _ in counter.most_common(top_n)]


def is_already_processed(post):
    """Check if a file already has valid type, summary, and keywords.

    Returns True if the entity has been fully processed and does not need
    reprocessing.
    """
    metadata = post.metadata
    entity_type = metadata.get('type')
    summary = metadata.get('summary')
    keywords = metadata.get('keywords')

    if not entity_type or entity_type not in CONFIG.get('entity_types', {}):
        return False

    if not summary or not isinstance(summary, str) or len(summary.strip()) < MIN_SUMMARY_LENGTH:
        return False

    if not keywords or not isinstance(keywords, list) or len(keywords) < 1:
        return False

    return True


def process_file(file_path):
    try:
        post = frontmatter.load(file_path)
        content = post.content
        title = post.get('title', file_path.stem)

        if is_already_processed(post):
            logger.info(f"[OK] Deja traite : {title} - ignore")
            return False

        logger.info(f"Classification de : {title}...")

        entity_type = detect_type(file_path, content)

        if entity_type not in CONFIG['entity_types']:
            logger.warning(f"[WARN] Type '{entity_type}' inconnu, classe comme 'Institution'")
            entity_type = "Institution"

        summary = generate_summary(content)
        keywords = extract_keywords(content)

        config = CONFIG['entity_types'][entity_type]
        target_folder = Path(config['folder'])
        target_folder.mkdir(exist_ok=True, parents=True)

        final_metadata = dict(post.metadata)
        final_metadata['type'] = entity_type
        final_metadata['summary'] = summary
        final_metadata['keywords'] = keywords

        existing_sources = final_metadata.get('sources', []) or []
        if not isinstance(existing_sources, list):
            existing_sources = [existing_sources]
        final_metadata['sources'] = existing_sources

        new_post = frontmatter.Post(content, **final_metadata)

        new_path = target_folder / file_path.name
        if file_path != new_path:
            shutil.move(str(file_path), str(new_path))
            logger.info(f"Deplace vers {target_folder}")

        with open(new_path, 'wb') as f:
            frontmatter.dump(new_post, f)

        logger.info(f"[OK] {title} classe en {entity_type}")
        return True

    except Exception as e:
        logger.error(f"[FAIL] Erreur sur {file_path} : {e}", exc_info=True)
        return True


def main():
    logger.info("Lancement du classificateur rule-based...")
    git.create_backup_tag()

    md_files = list(Path(".").rglob("*.md"))
    exclude_dirs = {".git", "scripts", "config", "admin"}
    md_files = [f for f in md_files
                if not any(part in exclude_dirs for part in f.parts)
                and f.name != "README.md"]

    total = len(md_files)
    skipped = 0
    processed = 0

    for i, f in enumerate(md_files):
        logger.info(f"Processing {i + 1}/{total}...")
        was_processed = process_file(f)

        if not was_processed:
            skipped += 1
        else:
            processed += 1

    logger.info(
        f"Termine : {processed} traites, {skipped} ignores sur {total} fichiers."
    )
    git.commit_changes("feat: classification rule-based et structuration des entites")
    logger.info("Termine.")


if __name__ == "__main__":
    main()

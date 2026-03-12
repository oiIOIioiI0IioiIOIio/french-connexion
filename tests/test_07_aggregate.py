"""
Tests pour 07_aggregate_public_sources.py

Tests unitaires avec mock des appels reseau pour valider le parsing
des reponses API, la creation de profils, et la gestion des doublons.
"""

import sys
import os
import json
import tempfile
import shutil
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Import du script avec prefixe numerique via importlib
_script_path = os.path.join(
    os.path.dirname(__file__), '..', 'scripts', '07_aggregate_public_sources.py'
)
_spec = importlib.util.spec_from_file_location("agg07", _script_path)
agg07 = importlib.util.module_from_spec(_spec)

# Ajouter le repertoire racine au path avant l'execution du module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
_spec.loader.exec_module(agg07)

# Enregistrer dans sys.modules pour que @patch puisse trouver le module
sys.modules["agg07"] = agg07

# Raccourcis vers les fonctions a tester
_normalize_name = agg07._normalize_name
_name_already_exists = agg07._name_already_exists
_safe_filename = agg07._safe_filename
_build_source_list = agg07._build_source_list
_build_bio_text = agg07._build_bio_text
_http_get_json = agg07._http_get_json
_parse_an_response = agg07._parse_an_response
_parse_senat_response = agg07._parse_senat_response
build_existing_index = agg07.build_existing_index
create_person_profile = agg07.create_person_profile
fetch_wikidata_category = agg07.fetch_wikidata_category
fetch_assemblee_deputes = agg07.fetch_assemblee_deputes
fetch_senat_senateurs = agg07.fetch_senat_senateurs
fetch_hatvp_for_person = agg07.fetch_hatvp_for_person
_parse_rne_person = agg07._parse_rne_person
_get_rne_field = agg07._get_rne_field
_discover_rne_resources = agg07._discover_rne_resources
_fetch_tabular_page = agg07._fetch_tabular_page
fetch_rne_deputes = agg07.fetch_rne_deputes
fetch_rne_senateurs = agg07.fetch_rne_senateurs


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_dir():
    """Cree un repertoire temporaire pour les tests de fichiers."""
    d = tempfile.mkdtemp(prefix="fc_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def empty_index():
    """Index vide pour les tests."""
    return set()


@pytest.fixture
def populated_index():
    """Index avec quelques noms pour tester les doublons."""
    return {"emmanuel macron", "alain juppe", "bernard arnault"}


# ============================================================
# Tests: _normalize_name
# ============================================================

class TestNormalizeName:
    def test_simple_name(self):
        assert _normalize_name("Jean Dupont") == "jean dupont"

    def test_hyphenated_name(self):
        assert _normalize_name("Jean-Pierre Dupont") == "jean pierre dupont"

    def test_whitespace(self):
        assert _normalize_name("  Marie Curie  ") == "marie curie"

    def test_uppercase(self):
        assert _normalize_name("ALAIN-JUPPE") == "alain juppe"

    def test_empty(self):
        assert _normalize_name("") == ""


# ============================================================
# Tests: _name_already_exists
# ============================================================

class TestNameAlreadyExists:
    def test_exact_match(self, populated_index):
        assert _name_already_exists("Emmanuel Macron", populated_index) is True

    def test_case_insensitive(self, populated_index):
        assert _name_already_exists("EMMANUEL MACRON", populated_index) is True

    def test_not_found(self, populated_index):
        assert _name_already_exists("Personne Inconnue", populated_index) is False

    def test_empty_index(self, empty_index):
        assert _name_already_exists("Anyone", empty_index) is False

    def test_hyphenated(self, populated_index):
        assert _name_already_exists("Alain-Juppe", populated_index) is True


# ============================================================
# Tests: _safe_filename
# ============================================================

class TestSafeFilename:
    def test_simple(self):
        assert _safe_filename("Jean Dupont") == "Jean-Dupont"

    def test_hyphenated(self):
        assert _safe_filename("Jean-Pierre Dupont") == "Jean-Pierre-Dupont"

    def test_special_chars(self):
        result = _safe_filename("Marie O'Brien")
        assert "/" not in result
        assert "'" not in result

    def test_empty(self):
        assert _safe_filename("") == ""


# ============================================================
# Tests: _build_source_list
# ============================================================

class TestBuildSourceList:
    def test_all_sources(self):
        data = {
            "wikidata_url": "https://www.wikidata.org/wiki/Q123",
            "url_nosdeputes": "https://www.nosdeputes.fr/jean-dupont",
            "url_nossenateurs": "https://www.nossenateurs.fr/jean-dupont",
            "hatvp_url": "https://www.hatvp.fr/?nom=Jean+Dupont",
        }
        sources = _build_source_list(data)
        assert len(sources) == 4

    def test_empty_data(self):
        sources = _build_source_list({})
        assert sources == []

    def test_partial_data(self):
        data = {"wikidata_url": "https://www.wikidata.org/wiki/Q123"}
        sources = _build_source_list(data)
        assert len(sources) == 1


# ============================================================
# Tests: _build_bio_text
# ============================================================

class TestBuildBioText:
    def test_depute(self):
        data = {
            "nom_complet": "Jean Dupont",
            "source_category": "depute",
            "date_naissance": "1970-01-15",
            "lieu_naissance": "Paris",
            "groupe_politique": "RE",
        }
        bio = _build_bio_text(data)
        assert "Jean Dupont" in bio
        assert "depute" in bio
        assert "1970-01-15" in bio
        assert "Paris" in bio
        assert "RE" in bio

    def test_senateur(self):
        data = {
            "nom_complet": "Marie Martin",
            "source_category": "senateur",
        }
        bio = _build_bio_text(data)
        assert "Marie Martin" in bio
        assert "senateur" in bio

    def test_minimal(self):
        data = {"nom_complet": "Test Person"}
        bio = _build_bio_text(data)
        assert "Test Person" in bio


# ============================================================
# Tests: Wikidata SPARQL response parsing
# ============================================================

class TestWikidataCategory:
    """Test le parsing des reponses Wikidata SPARQL."""

    MOCK_WIKIDATA_RESPONSE = {
        "results": {
            "bindings": [
                {
                    "person": {"value": "http://www.wikidata.org/entity/Q1234"},
                    "personLabel": {"value": "Jacques Dupont"},
                    "birthDate": {"value": "1955-03-20T00:00:00Z"},
                    "birthPlaceLabel": {"value": "Lyon"},
                    "genderLabel": {"value": "masculin"},
                    "occupationLabel": {"value": "homme politique"},
                    "educationLabel": {"value": "Sciences Po"},
                },
                {
                    "person": {"value": "http://www.wikidata.org/entity/Q5678"},
                    "personLabel": {"value": "Q5678"},  # ID brut = skip
                    "birthDate": {"value": "1960-01-01T00:00:00Z"},
                },
                {
                    "person": {"value": "http://www.wikidata.org/entity/Q9012"},
                    "personLabel": {"value": "Marie Lefevre"},
                    "birthDate": {"value": "1972-07-14T00:00:00Z"},
                    "birthPlaceLabel": {"value": "Marseille"},
                    "genderLabel": {"value": "feminin"},
                },
            ]
        }
    }

    @patch("agg07._http_get_json")
    def test_parse_valid_response(self, mock_get):
        mock_get.return_value = self.MOCK_WIKIDATA_RESPONSE
        results = fetch_wikidata_category("politiciens", "Q82955", 10)
        # Q5678 is skipped (starts with Q)
        assert len(results) == 2
        assert results[0]["nom_complet"] == "Jacques Dupont"
        assert results[0]["date_naissance"] == "1955-03-20"
        assert results[0]["lieu_naissance"] == "Lyon"
        assert results[0]["genre"] == "masculin"
        assert results[0]["occupation"] == "homme politique"
        assert results[0]["formation"] == "Sciences Po"
        assert results[1]["nom_complet"] == "Marie Lefevre"

    @patch("agg07._http_get_json")
    def test_handle_empty_response(self, mock_get):
        mock_get.return_value = {"results": {"bindings": []}}
        results = fetch_wikidata_category("politiciens", "Q82955", 10)
        assert results == []

    @patch("agg07._http_get_json")
    def test_handle_network_error(self, mock_get):
        mock_get.return_value = None
        results = fetch_wikidata_category("politiciens", "Q82955", 10)
        assert results == []


# ============================================================
# Tests: Assemblee Nationale response parsing
# ============================================================

class TestAssembleeDeputes:
    """Test le parsing des reponses NosDonnees.fr."""

    MOCK_AN_RESPONSE = {
        "deputes": [
            {
                "depute": {
                    "nom": "Marie Martin",
                    "slug": "marie-martin",
                    "date_naissance": "1975-05-20",
                    "lieu_naissance": "Toulouse",
                    "sexe": "F",
                    "groupe_sigle": "RE",
                    "parti_ratt_financier": "Renaissance",
                    "profession": "avocate",
                }
            },
            {
                "depute": {
                    "nom": "Pierre Duval",
                    "slug": "pierre-duval",
                    "date_naissance": "1968-11-03",
                    "sexe": "H",
                    "groupe_sigle": "LFI",
                }
            },
            {
                "depute": {
                    "nom": "",  # Nom vide = skip
                }
            },
        ]
    }

    # Format alternatif sans wrapper "depute"
    MOCK_AN_RESPONSE_FLAT = [
        {
            "nom": "Marie Martin",
            "slug": "marie-martin",
            "date_naissance": "1975-05-20",
            "lieu_naissance": "Toulouse",
            "sexe": "F",
            "groupe_sigle": "RE",
        },
    ]

    @patch("agg07._http_get_json")
    def test_parse_valid_response(self, mock_get):
        mock_get.return_value = self.MOCK_AN_RESPONSE
        results = fetch_assemblee_deputes()
        # 2 valides (nom vide est filtre)
        assert len(results) == 2
        assert results[0]["nom_complet"] == "Marie Martin"
        assert results[0]["source"] == "assemblee_nationale"
        assert results[0]["date_naissance"] == "1975-05-20"
        assert results[0]["genre"] == "feminin"
        assert results[0]["groupe_politique"] == "RE"
        assert results[0]["parti"] == "Renaissance"
        assert results[0]["profession_origine"] == "avocate"
        assert results[1]["nom_complet"] == "Pierre Duval"
        assert results[1]["genre"] == "masculin"

    @patch("agg07._http_get_json")
    def test_handle_network_error(self, mock_get):
        mock_get.return_value = None
        results = fetch_assemblee_deputes()
        assert results == []

    def test_parse_an_response_flat_list(self):
        """Test le parsing d'une reponse au format liste directe."""
        result = _parse_an_response(self.MOCK_AN_RESPONSE_FLAT)
        assert len(result) == 1

    def test_parse_an_response_none(self):
        """Test que None retourne une liste vide."""
        result = _parse_an_response(None)
        assert result == []

    def test_parse_an_response_empty_dict(self):
        """Test qu'un dict vide retourne une liste vide."""
        result = _parse_an_response({})
        assert result == []

    @patch("agg07._http_get_json")
    def test_flat_response_format(self, mock_get):
        """Test le parsing quand l'API retourne une liste directe."""
        mock_get.return_value = self.MOCK_AN_RESPONSE_FLAT
        results = fetch_assemblee_deputes()
        assert len(results) == 1
        assert results[0]["nom_complet"] == "Marie Martin"


# ============================================================
# Tests: Senat response parsing
# ============================================================

class TestSenatSenateurs:
    """Test le parsing des reponses NosSenateurs.fr."""

    MOCK_SENAT_RESPONSE = {
        "senateurs": [
            {
                "senateur": {
                    "nom": "Henri Laporte",
                    "slug": "henri-laporte",
                    "date_naissance": "1960-02-14",
                    "lieu_naissance": "Nice",
                    "sexe": "H",
                    "groupe_sigle": "LR",
                    "profession": "medecin",
                }
            },
        ]
    }

    # Format alternatif sans wrapper "senateur"
    MOCK_SENAT_RESPONSE_FLAT = [
        {
            "nom": "Henri Laporte",
            "slug": "henri-laporte",
            "date_naissance": "1960-02-14",
            "lieu_naissance": "Nice",
            "sexe": "H",
            "groupe_sigle": "LR",
        },
    ]

    @patch("agg07._http_get_json")
    def test_parse_valid_response(self, mock_get):
        mock_get.return_value = self.MOCK_SENAT_RESPONSE
        results = fetch_senat_senateurs()
        assert len(results) == 1
        assert results[0]["nom_complet"] == "Henri Laporte"
        assert results[0]["source"] == "senat"
        assert results[0]["occupation"] == "senateur"

    @patch("agg07._http_get_json")
    def test_handle_network_error(self, mock_get):
        mock_get.return_value = None
        results = fetch_senat_senateurs()
        assert results == []

    def test_parse_senat_response_flat_list(self):
        """Test le parsing d'une reponse au format liste directe."""
        result = _parse_senat_response(self.MOCK_SENAT_RESPONSE_FLAT)
        assert len(result) == 1

    def test_parse_senat_response_none(self):
        """Test que None retourne une liste vide."""
        result = _parse_senat_response(None)
        assert result == []

    @patch("agg07._http_get_json")
    def test_flat_response_format(self, mock_get):
        """Test le parsing quand l'API retourne une liste directe."""
        mock_get.return_value = self.MOCK_SENAT_RESPONSE_FLAT
        results = fetch_senat_senateurs()
        assert len(results) == 1
        assert results[0]["nom_complet"] == "Henri Laporte"


# ============================================================
# Tests: HATVP
# ============================================================

class TestHATVP:
    """Test l'enrichissement HATVP via index CSV."""

    MOCK_CSV_DATA = [
        {
            "prenom": "Jean",
            "nom": "Dupont",
            "qualite": "Depute",
            "type_mandat": "Depute",
            "date_publication": "2024-06-15",
        },
        {
            "prenom": "Jean",
            "nom": "Dupont",
            "qualite": "Depute",
            "type_mandat": "Depute",
            "date_publication": "2023-01-10",
        },
    ]

    @patch("agg07._load_hatvp_index")
    def test_declaration_found(self, mock_index):
        mock_index.return_value = self.MOCK_CSV_DATA
        result = fetch_hatvp_for_person("Jean Dupont")
        assert result["hatvp_declared"] is True
        assert result["hatvp_function"] == "Depute"
        assert "hatvp_url" in result

    @patch("agg07._load_hatvp_index")
    def test_no_declaration(self, mock_index):
        mock_index.return_value = self.MOCK_CSV_DATA
        result = fetch_hatvp_for_person("Inconnu Personne")
        assert result == {}

    @patch("agg07._load_hatvp_index")
    def test_empty_index(self, mock_index):
        mock_index.return_value = []
        result = fetch_hatvp_for_person("Test Personne")
        assert result == {}

    @patch("agg07._load_hatvp_index")
    def test_short_name(self, mock_index):
        mock_index.return_value = self.MOCK_CSV_DATA
        result = fetch_hatvp_for_person("Jean")
        assert result == {}

    @patch("agg07._load_hatvp_index")
    def test_picks_latest_declaration(self, mock_index):
        """Test que la declaration la plus recente est selectionnee."""
        mock_index.return_value = self.MOCK_CSV_DATA
        result = fetch_hatvp_for_person("Jean Dupont")
        assert result["hatvp_declared"] is True


# ============================================================
# Tests: Profile creation
# ============================================================

class TestCreateProfile:
    def test_create_valid_profile(self, temp_dir, empty_index):
        """Test la creation d'une fiche personne valide."""
        person = {
            "nom_complet": "Test-Unique-Person-XYZ",
            "source": "wikidata_sparql",
            "source_category": "politiciens",
            "date_naissance": "1980-01-01",
            "lieu_naissance": "Paris",
            "occupation": "homme politique",
        }

        # Changer le dossier de travail temporairement
        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            Path("personnes").mkdir(exist_ok=True)
            path = create_person_profile(person, empty_index)
            assert path is not None
            assert Path(path).exists()

            # Verifier le contenu
            import frontmatter
            post = frontmatter.load(path)
            assert post.metadata["type"] == "Personne"
            assert post.metadata["nom_complet"] == "Test-Unique-Person-XYZ"
            # Verifier les noms de champs compatibles avec le site web
            assert post.metadata["birth_date"] == "1980-01-01"
            assert post.metadata["birth_place"] == "Paris"
            assert post.metadata["nationality"] == "francaise"
            assert post.metadata["occupation"] == "homme politique"
            assert "summary" in post.metadata
            assert "Test-Unique-Person-XYZ" in post.content
        finally:
            os.chdir(old_cwd)

    def test_skip_duplicate(self, populated_index):
        """Test que les doublons sont ignores."""
        person = {
            "nom_complet": "Emmanuel Macron",
            "source": "test",
        }
        result = create_person_profile(person, populated_index)
        assert result is None

    def test_skip_short_name(self, empty_index):
        """Test que les noms trop courts sont ignores."""
        person = {
            "nom_complet": "Ab",
            "source": "test",
        }
        result = create_person_profile(person, empty_index)
        assert result is None

    def test_skip_empty_name(self, empty_index):
        """Test que les noms vides sont ignores."""
        person = {
            "nom_complet": "",
            "source": "test",
        }
        result = create_person_profile(person, empty_index)
        assert result is None


# ============================================================
# Tests: RNE / API tabulaire data.gouv.fr
# ============================================================

class TestGetRneField:
    """Test l'extraction de champs RNE avec noms de colonnes variables."""

    def test_first_key_match(self):
        row = {"Nom de l'élu": "DUPONT", "nom": "ignored"}
        assert _get_rne_field(row, "Nom de l'élu", "nom") == "DUPONT"

    def test_fallback_key(self):
        row = {"nom_elu": "MARTIN"}
        assert _get_rne_field(row, "Nom de l'élu", "nom_elu") == "MARTIN"

    def test_no_match(self):
        row = {"other": "value"}
        assert _get_rne_field(row, "Nom de l'élu", "nom_elu") == ""

    def test_empty_value_skipped(self):
        row = {"Nom de l'élu": "", "nom": "DUVAL"}
        assert _get_rne_field(row, "Nom de l'élu", "nom") == "DUVAL"

    def test_whitespace_stripped(self):
        row = {"nom": "  BLANC  "}
        assert _get_rne_field(row, "nom") == "BLANC"


class TestParseRnePerson:
    """Test le parsing d'une ligne RNE en dict personne."""

    def test_valid_row(self):
        row = {
            "Nom de l'élu": "DUPONT",
            "Prénom de l'élu": "Jean",
            "Date de naissance": "1970-05-15",
            "Code sexe": "M",
            "Libellé de la profession": "Avocat",
            "Libellé du département": "Paris",
            "Date de début du mandat": "2022-06-19",
        }
        p = _parse_rne_person(row, "depute")
        assert p is not None
        assert p["nom_complet"] == "Jean DUPONT"
        assert p["source"] == "rne_datagouv"
        assert p["source_category"] == "depute"
        assert p["date_naissance"] == "1970-05-15"
        assert p["genre"] == "masculin"
        assert p["profession_origine"] == "Avocat"
        assert p["departement"] == "Paris"
        assert p["date_debut_mandat"] == "2022-06-19"
        assert "url_datagouv" in p

    def test_female(self):
        row = {
            "Nom de l'élu": "MARTIN",
            "Prénom de l'élu": "Marie",
            "Code sexe": "F",
        }
        p = _parse_rne_person(row, "senateur")
        assert p is not None
        assert p["genre"] == "feminin"

    def test_missing_name(self):
        row = {"Nom de l'élu": "", "Prénom de l'élu": "Jean"}
        assert _parse_rne_person(row, "depute") is None

    def test_missing_prenom(self):
        row = {"Nom de l'élu": "DUPONT", "Prénom de l'élu": ""}
        assert _parse_rne_person(row, "depute") is None

    def test_empty_row(self):
        assert _parse_rne_person({}, "depute") is None

    def test_alternative_column_names(self):
        """Test avec des noms de colonnes alternatifs."""
        row = {
            "nom": "BLANC",
            "prenom": "Pierre",
            "date_naissance": "1980-01-01",
            "sexe": "H",
        }
        p = _parse_rne_person(row, "depute")
        assert p is not None
        assert p["nom_complet"] == "Pierre BLANC"
        assert p["genre"] == "masculin"

    def test_date_with_timestamp(self):
        """Test que les dates avec timestamp sont nettoyees."""
        row = {
            "Nom de l'élu": "DUVAL",
            "Prénom de l'élu": "Luc",
            "Date de naissance": "1975-03-10T00:00:00Z",
        }
        p = _parse_rne_person(row, "depute")
        assert p["date_naissance"] == "1975-03-10"


class TestDiscoverRneResources:
    """Test la decouverte des resource IDs RNE."""

    MOCK_DATASET_RESPONSE = {
        "resources": [
            {
                "id": "aaaa-1111-bbbb-deputes",
                "title": "RNE - Les députés",
                "format": "csv",
            },
            {
                "id": "aaaa-2222-bbbb-senateurs",
                "title": "RNE - Les sénateurs",
                "format": "csv",
            },
            {
                "id": "aaaa-3333-bbbb-maires",
                "title": "RNE - Les maires",
                "format": "csv",
            },
            {
                "id": "aaaa-4444-bbbb-schema",
                "title": "Schema de validation",
                "format": "json",
            },
        ]
    }

    @patch("agg07._http_get_json")
    def test_discover_resources(self, mock_get):
        agg07._rne_resource_cache = None
        mock_get.return_value = self.MOCK_DATASET_RESPONSE
        result = _discover_rne_resources()
        assert "deputes" in result
        assert result["deputes"] == "aaaa-1111-bbbb-deputes"
        assert "senateurs" in result
        assert result["senateurs"] == "aaaa-2222-bbbb-senateurs"
        assert "maires" in result
        agg07._rne_resource_cache = None

    @patch("agg07._http_get_json")
    def test_discover_network_error(self, mock_get):
        agg07._rne_resource_cache = None
        mock_get.return_value = None
        result = _discover_rne_resources()
        assert result == {}
        agg07._rne_resource_cache = None

    @patch("agg07._http_get_json")
    def test_discover_empty_resources(self, mock_get):
        agg07._rne_resource_cache = None
        mock_get.return_value = {"resources": []}
        result = _discover_rne_resources()
        assert result == {}
        agg07._rne_resource_cache = None

    @patch("agg07._http_get_json")
    def test_skips_non_csv(self, mock_get):
        """Test que les ressources non-CSV sont ignorees."""
        agg07._rne_resource_cache = None
        mock_get.return_value = {
            "resources": [
                {"id": "abc", "title": "deputes schema", "format": "json"},
            ]
        }
        result = _discover_rne_resources()
        assert "deputes" not in result
        agg07._rne_resource_cache = None


class TestFetchTabularPage:
    """Test la recuperation de donnees via l'API tabulaire."""

    @patch("agg07._http_get_json")
    def test_fetch_page(self, mock_get):
        mock_get.return_value = {
            "data": [{"col": "val"}],
            "meta": {"page": 1, "page_size": 50, "total": 1},
        }
        result = _fetch_tabular_page("some-rid", page=1, page_size=50)
        assert result is not None
        assert len(result["data"]) == 1

    @patch("agg07._http_get_json")
    def test_fetch_page_network_error(self, mock_get):
        mock_get.return_value = None
        result = _fetch_tabular_page("some-rid")
        assert result is None


class TestFetchRneDeputes:
    """Test la recuperation des deputes via le RNE."""

    MOCK_TABULAR_RESPONSE = {
        "data": [
            {
                "Nom de l'élu": "DUPONT",
                "Prénom de l'élu": "Jean",
                "Date de naissance": "1970-05-15",
                "Code sexe": "M",
            },
            {
                "Nom de l'élu": "MARTIN",
                "Prénom de l'élu": "Marie",
                "Date de naissance": "1980-03-10",
                "Code sexe": "F",
            },
        ],
        "meta": {"page": 1, "page_size": 50, "total": 2},
    }

    @patch("agg07._fetch_tabular_page")
    @patch("agg07._discover_rne_resources")
    def test_fetch_rne_deputes(self, mock_discover, mock_fetch):
        mock_discover.return_value = {"deputes": "fake-rid"}
        mock_fetch.return_value = self.MOCK_TABULAR_RESPONSE
        results = fetch_rne_deputes()
        assert len(results) == 2
        assert results[0]["nom_complet"] == "Jean DUPONT"
        assert results[0]["source"] == "rne_datagouv"
        assert results[1]["nom_complet"] == "Marie MARTIN"

    @patch("agg07._discover_rne_resources")
    def test_no_resource_id(self, mock_discover):
        mock_discover.return_value = {}
        results = fetch_rne_deputes()
        assert results == []

    @patch("agg07._fetch_tabular_page")
    @patch("agg07._discover_rne_resources")
    def test_empty_data(self, mock_discover, mock_fetch):
        mock_discover.return_value = {"deputes": "fake-rid"}
        mock_fetch.return_value = {"data": [], "meta": {"total": 0}}
        results = fetch_rne_deputes()
        assert results == []


class TestBuildSourceListWithDatagouv:
    """Test que _build_source_list inclut les liens data.gouv.fr."""

    def test_datagouv_url_included(self):
        data = {
            "url_datagouv": "https://www.data.gouv.fr/datasets/rne/",
        }
        sources = _build_source_list(data)
        assert len(sources) == 1
        assert "data.gouv.fr" in sources[0]

    def test_all_sources_including_datagouv(self):
        data = {
            "wikidata_url": "https://www.wikidata.org/wiki/Q123",
            "url_nosdeputes": "https://www.nosdeputes.fr/jean",
            "url_nossenateurs": "https://www.nossenateurs.fr/jean",
            "hatvp_url": "https://www.hatvp.fr/?nom=Jean",
            "url_datagouv": "https://www.data.gouv.fr/datasets/rne/",
        }
        sources = _build_source_list(data)
        assert len(sources) == 5

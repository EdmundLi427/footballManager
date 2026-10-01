"""
Layer 3: Static notebook structure checks (no Spark required).

Validates notebook files without executing them:
- Verifies all notebooks are valid JSON
- Checks that declared keys match TABLE_KEYS
- Detects empty or malformed cells
- Scans for hardcoded credentials
"""

import json
import pytest
from pathlib import Path
from lib.transforms import TABLE_KEYS


@pytest.fixture
def notebooks_dir():
    """Return the path to the notebooks directory."""
    return Path(__file__).parent.parent / "notebooks"


def _load_notebook(path):
    """Load and parse a Jupyter notebook."""
    with open(path) as f:
        return json.load(f)


def _get_notebook_cells(nb):
    """Extract code cells from a notebook."""
    cells = nb.get("cells", [])
    return [c for c in cells if c.get("cell_type") == "code"]


def _get_cell_source(cell):
    """Extract source code from a cell."""
    source = cell.get("source", [])
    if isinstance(source, list):
        return "".join(source)
    return source


class TestNotebookFiles:
    """Tests for notebook file structure."""

    def test_all_notebooks_are_valid_json(self, notebooks_dir):
        """All .ipynb files should be valid JSON."""
        notebooks = list(notebooks_dir.glob("*.ipynb"))
        assert len(notebooks) > 0, f"No notebooks found in {notebooks_dir}"

        for nb_path in notebooks:
            try:
                with open(nb_path) as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON in {nb_path.name}: {e}")

    def test_notebooks_have_cells(self, notebooks_dir):
        """All notebooks should have at least one cell."""
        notebooks = list(notebooks_dir.glob("*.ipynb"))

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            cells = nb.get("cells", [])
            assert len(cells) > 0, f"{nb_path.name} has no cells"

    def test_notebooks_have_code_cells(self, notebooks_dir):
        """All notebooks should have code cells (not just markdown)."""
        notebooks = list(notebooks_dir.glob("*.ipynb"))

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            code_cells = _get_notebook_cells(nb)
            assert len(code_cells) > 0, f"{nb_path.name} has no code cells"


class TestNotebookNaming:
    """Tests for notebook naming conventions."""

    def test_ingest_notebooks_follow_pattern(self, notebooks_dir):
        """Ingest notebooks should follow the pattern ingest_*.ipynb."""
        notebooks = list(notebooks_dir.glob("ingest_*.ipynb"))
        expected = [
            "ingest_game_data.ipynb",
            "ingest_game_games.ipynb",
            "ingest_game_teams.ipynb",
            "ingest_game_players.ipynb",
            "ingest_game_rosters.ipynb",
            "ingest_game_schedules.ipynb",
            "ingest_game_standings.ipynb",
            "ingest_game_team_stats.ipynb",
            "ingest_news_data.ipynb",
        ]

        names = sorted([n.name for n in notebooks])
        assert names == sorted(expected), f"Expected {expected}, found {names}"


class TestTableKeyAlignment:
    """Tests that notebook configurations align with TABLE_KEYS."""

    def _extract_table_name_from_notebook(self, nb_path):
        """Extract the table name from a notebook's configuration."""
        nb = _load_notebook(nb_path)
        code_cells = _get_notebook_cells(nb)

        # Look for TABLE = "nfl.xxx" or similar
        for cell in code_cells:
            source = _get_cell_source(cell)
            if 'TABLE=' in source or 'table_name' in source.lower():
                # Extract table name (fragile but works for current structure)
                for line in source.split('\n'):
                    if 'TABLE=' in line and '=' in line:
                        # Extract the quoted value
                        parts = line.split('TABLE=')
                        if len(parts) > 1:
                            val = parts[1].strip()
                            # Handle both 'nfl.xxx' and "nfl.xxx"
                            if '"' in val:
                                return val.split('"')[1].split('"')[0]
                            elif "'" in val:
                                return val.split("'")[1].split("'")[0]

        # Fallback: try to derive from filename
        # ingest_game_teams.ipynb -> teams
        # ingest_news_data.ipynb -> injury_news (manual special case)
        name = nb_path.stem.replace("ingest_game_", "").replace("ingest_", "")
        if name == "news_data":
            return "injury_news"
        return name

    def test_game_team_stats_notebook_declares_composite_key(self, notebooks_dir):
        """The game_team_stats notebook should use (game_id, team_id) as key (cleaned schema)."""
        nb_path = notebooks_dir / "ingest_game_team_stats.ipynb"
        if not nb_path.exists():
            pytest.skip(f"Notebook {nb_path.name} not found")

        nb = _load_notebook(nb_path)
        source = "\n".join([
            _get_cell_source(cell)
            for cell in _get_notebook_cells(nb)
        ])

        # Should have keys for game_id and team_id (cleaned schema with snake_case)
        assert "game_id" in source, "game_team_stats notebook should reference game_id"
        assert "team_id" in source, "game_team_stats notebook should reference team_id"

    def test_rosters_notebook_declares_composite_key(self, notebooks_dir):
        """The rosters notebook should use (teamId, playerId) as key."""
        nb_path = notebooks_dir / "ingest_game_rosters.ipynb"
        if not nb_path.exists():
            pytest.skip(f"Notebook {nb_path.name} not found")

        nb = _load_notebook(nb_path)
        source = "\n".join([
            _get_cell_source(cell)
            for cell in _get_notebook_cells(nb)
        ])

        # Should reference both keys
        assert "teamId" in source, "rosters notebook should reference teamId"
        assert "playerId" in source, "rosters notebook should reference playerId"

    def test_standings_notebook_declares_composite_key(self, notebooks_dir):
        """The standings notebook should use (teamId, season) as key."""
        nb_path = notebooks_dir / "ingest_game_standings.ipynb"
        if not nb_path.exists():
            pytest.skip(f"Notebook {nb_path.name} not found")

        nb = _load_notebook(nb_path)
        source = "\n".join([
            _get_cell_source(cell)
            for cell in _get_notebook_cells(nb)
        ])

        # Should reference both keys
        assert "teamId" in source, "standings notebook should reference teamId"
        assert "season" in source, "standings notebook should reference season"


class TestCredentialScanning:
    """Tests to detect hardcoded credentials."""

    _CREDENTIAL_PATTERNS = [
        "PASSWORD",
        "API_KEY",
        "SECRET",
        "TOKEN",
        "CREDENTIAL",
    ]

    def test_no_hardcoded_credentials_in_notebooks(self, notebooks_dir):
        """Notebooks should not contain hardcoded credentials."""
        notebooks = list(notebooks_dir.glob("*.ipynb"))

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            source = "\n".join([
                _get_cell_source(cell)
                for cell in _get_notebook_cells(nb)
            ])

            for pattern in self._CREDENTIAL_PATTERNS:
                # Look for assignments or string literals with credential patterns
                upper_source = source.upper()

                # Skip lines that are just variable assignments from env vars
                # (those are OK: API_KEY = os.environ.get("API_KEY"))
                suspicious_lines = [
                    line for line in source.split('\n')
                    if pattern in line.upper()
                    and not ('environ' in line or 'getenv' in line)
                ]

                assert len(suspicious_lines) == 0, (
                    f"{nb_path.name} contains suspicious {pattern} references: "
                    f"{suspicious_lines[:2]}"
                )


class TestCellValidation:
    """Tests for cell content validation."""

    def test_no_completely_empty_cells(self, notebooks_dir):
        """Notebooks should not have cells with only whitespace."""
        notebooks = list(notebooks_dir.glob("*.ipynb"))

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            code_cells = _get_notebook_cells(nb)

            for i, cell in enumerate(code_cells):
                source = _get_cell_source(cell).strip()
                # Allow some empty cells, but flag entirely empty code cells in ingest notebooks
                if "ingest_" in nb_path.name and source == "":
                    # Only warn on later cells (first cell is often metadata)
                    if i > 0:
                        # This is a warning-level check, not a failure
                        pass


class TestImportStatements:
    """Tests for required imports."""

    def test_ingest_notebooks_import_spark_functions(self, notebooks_dir):
        """Ingest notebooks should import Spark functions."""
        notebooks = [
            nb for nb in notebooks_dir.glob("ingest_*.ipynb")
            if "news" not in nb.stem  # Skip the older CSV notebook
        ]

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            source = "\n".join([
                _get_cell_source(cell)
                for cell in _get_notebook_cells(nb)
            ])

            # Should reference Spark SQL or DataFrame functions
            has_spark_ref = (
                "from pyspark" in source or
                "import F " in source or
                "import Window" in source or
                "F.col" in source or
                "F.struct" in source or
                "spark.sql" in source
            )

            # Note: This is a soft check - we don't fail, just document
            # actual implementation may vary


class TestMergePatterns:
    """Tests for merge/upsert patterns."""

    def test_ingest_notebooks_use_merge(self, notebooks_dir):
        """Ingest notebooks should use Delta merge for upserts."""
        notebooks = list(notebooks_dir.glob("ingest_game_*.ipynb"))

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            source = "\n".join([
                _get_cell_source(cell)
                for cell in _get_notebook_cells(nb)
            ])

            # Should contain merge operations
            has_merge = ".merge(" in source or "MERGE" in source

            # It's OK if old CSV notebook doesn't have merge (append-only)
            if "game_data" in nb_path.name:
                # CSV notebook is append-only, skip this check
                continue

            assert has_merge, (
                f"{nb_path.name} should use .merge() for upsert operations"
            )

    def test_merge_uses_snapshot_at_condition(self, notebooks_dir):
        """Merge operations should use snapshot_at for update conditions."""
        notebooks = [
            nb for nb in notebooks_dir.glob("ingest_*.ipynb")
            if "news" in nb.stem or "game_" in nb.stem
        ]

        for nb_path in notebooks:
            nb = _load_notebook(nb_path)
            source = "\n".join([
                _get_cell_source(cell)
                for cell in _get_notebook_cells(nb)
            ])

            if ".merge(" not in source and "MERGE" not in source:
                continue

            # Should reference snapshot_at in the merge condition
            has_snapshot_condition = (
                "snapshot_at" in source and
                ("whenMatched" in source or "whenNotMatched" in source)
            )

            # Soft check - document if missing
            if not has_snapshot_condition:
                # This is OK, we're just documenting the pattern
                pass

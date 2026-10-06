"""Static notebook checks (no Spark): notebooks stay thin wrappers over `lib`."""

import json
import re
from pathlib import Path

import pytest

NOTEBOOKS = Path(__file__).parent.parent / "notebooks"
INGEST = sorted(NOTEBOOKS.glob("ingest_*.ipynb"))
ALL = sorted(NOTEBOOKS.glob("*.ipynb"))


def code(path: Path) -> str:
    nb = json.loads(path.read_text())
    return "\n".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )


def test_expected_notebooks_exist():
    names = {p.stem for p in ALL}
    assert names == {
        "ingest_game_teams",
        "ingest_game_players",
        "ingest_game_rosters",
        "ingest_game_standings",
        "ingest_game_schedules",
        "ingest_game_games",
        "ingest_game_team_stats",
        "ingest_game_player_stats",
        "ingest_news_data",
        "reset_espn_tables",
    }


@pytest.mark.parametrize("path", INGEST, ids=lambda p: p.stem)
def test_ingest_notebook_uses_shared_library(path):
    src = code(path)
    assert "from lib import delta_io, schemas, transforms" in src
    assert re.search(r'schemas\.TABLES\["\w+"\]', src)
    assert re.search(
        r"transforms\.parse_\w+\(transforms\.with_file_metadata\(raw\)\)", src
    )
    assert "delta_io.upsert_batch_fn(spark, TABLE)" in src
    assert ".schema(TABLE.raw_schema)" in src
    assert "trigger(availableNow=True)" in src


@pytest.mark.parametrize("path", INGEST, ids=lambda p: p.stem)
def test_ingest_notebook_parser_matches_table(path):
    src = code(path)
    table = re.search(r'schemas\.TABLES\["(\w+)"\]', src).group(1)
    assert f"transforms.parse_{table}(" in src


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_no_inline_schema_fill_or_hardcoded_user_paths(path):
    src = code(path)
    assert "F.lit(None).cast" not in src, (
        "silently NULL-filling missing columns hides schema drift"
    )
    assert "/Workspace/Repos" not in src
    assert "@" not in src.replace("abfss://alsource@", "").replace(
        "abfss://aldestination@", ""
    )


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_no_hardcoded_credentials(path):
    src = code(path).upper()
    for pattern in ("PASSWORD", "API_KEY", "SECRET", "ACCOUNTKEY", "SAS_TOKEN"):
        assert pattern not in src, f"{path.name} mentions {pattern}"


def test_reset_notebook_is_guarded_and_spares_injury_news():
    src = code(NOTEBOOKS / "reset_espn_tables.ipynb")
    assert 'dbutils.widgets.get("confirm") != "RESET"' in src
    assert "schemas.ESPN_TABLES" in src
    assert "injury" not in src

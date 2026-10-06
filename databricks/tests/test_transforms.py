"""Parser tests on real ESPN landing fixtures (recorded by azure/scripts/refresh-espn-fixtures.js).

Golden values come from DEN @ KC, 2026 week 1 (ESPN event 401872931), cross-checked against
the raw API response.
"""

from datetime import date, datetime

import pytest
from lib.schemas import TABLES
from lib.transforms import PARSERS

pytestmark = pytest.mark.spark

GAME_ID = "401872931"


def parse(read_fixture, table):
    return PARSERS[table](read_fixture(table))


def by_key(rows, *keys):
    return {
        tuple(getattr(r, k) for k in keys) if len(keys) > 1 else getattr(r, keys[0]): r
        for r in rows
    }


@pytest.mark.parametrize("table", list(TABLES))
def test_output_matches_table_schema(read_fixture, table):
    df = parse(read_fixture, table)
    actual = [(f.name, f.dataType) for f in df.schema.fields]
    expected = [(f.name, f.dataType) for f in TABLES[table].schema.fields]
    assert actual == expected


@pytest.mark.parametrize("table", list(TABLES))
def test_rows_present_keys_unique_and_not_null(read_fixture, table):
    rows = parse(read_fixture, table).collect()
    keys = TABLES[table].keys
    assert rows, f"{table}: parser produced no rows from fixture"
    for row in rows:
        assert all(getattr(row, k) is not None for k in keys), (
            f"{table}: null key in {row}"
        )
    assert len({tuple(getattr(r, k) for k in keys) for r in rows}) == len(rows)


def test_audit_columns_populated(read_fixture):
    row = parse(read_fixture, "schedules").first()
    assert row._source_file.endswith("schedules.json")
    assert isinstance(row._snapshot_at, datetime)
    assert isinstance(row._ingested_at, datetime)


def test_game_team_stats_golden_values(read_fixture):
    rows = by_key(
        parse(read_fixture, "game_team_stats").collect(), "game_id", "team_id"
    )
    assert set(rows) == {(GAME_ID, "12"), (GAME_ID, "7")}

    kc = rows[(GAME_ID, "12")]
    assert kc.team_abbreviation == "KC"
    assert kc.home_away == "home"  # ESPN lists the away team first in boxscore.teams
    assert (kc.third_down_conversions, kc.third_down_attempts) == (9, 17)
    assert (kc.fourth_down_conversions, kc.fourth_down_attempts) == (2, 3)
    assert (kc.pass_completions, kc.pass_attempts) == (15, 27)
    assert (kc.sacks, kc.sack_yards_lost) == (2, 12)
    assert (kc.red_zone_scores, kc.red_zone_attempts) == (3, 5)
    assert (kc.penalties, kc.penalty_yards) == (7, 60)
    assert kc.possession_seconds == 33 * 60 + 42
    assert kc.total_yards == 392
    assert kc.rushing_yards == 220
    assert kc.interceptions_thrown == 1
    assert kc.turnovers == 1
    assert kc.yards_per_play == pytest.approx(5.8507, abs=1e-3)

    den = rows[(GAME_ID, "7")]
    assert den.home_away == "away"
    assert (den.pass_completions, den.pass_attempts) == (17, 28)
    assert den.possession_seconds == 26 * 60 + 18
    assert kc.possession_seconds + den.possession_seconds == 60 * 60


def test_player_game_stats_golden_values(read_fixture):
    rows = by_key(parse(read_fixture, "player_game_stats").collect(), "player_id")
    assert all(r.game_id == GAME_ID for r in rows.values())

    # Passing + fumbles groups merge into one row; 'interceptions' thrown, not caught.
    nix = rows["4426338"]
    assert (nix.player_name, nix.team_id, nix.team_abbreviation) == (
        "Bo Nix",
        "7",
        "DEN",
    )
    assert (nix.season, nix.season_type, nix.week) == (2026, 2, 1)
    assert (nix.pass_completions, nix.pass_attempts) == (17, 28)
    assert (nix.passing_yards, nix.passing_tds, nix.interceptions_thrown) == (131, 1, 1)
    assert (nix.sacks_taken, nix.sack_yards_lost) == (4, 16)
    assert nix.passer_rating == pytest.approx(69.2)
    assert nix.qbr == pytest.approx(11.4)
    assert (nix.fumbles, nix.fumbles_lost) == (3, 1)
    assert nix.def_interceptions is None
    assert nix.rushing_yards is None  # not in the trimmed fixture's rushing group

    # Rushing + receiving.
    johnson = rows["4832955"]
    assert johnson.team_abbreviation == "KC"
    assert (johnson.rushing_attempts, johnson.rushing_yards, johnson.rushing_long) == (
        8,
        24,
        6,
    )
    assert (johnson.receptions, johnson.targets, johnson.receiving_yards) == (2, 2, 44)

    walker = rows["4567048"]
    assert (walker.rushing_yards, walker.rushing_tds, walker.rushing_long) == (
        173,
        1,
        60,
    )

    jones = rows["4039059"]
    assert (jones.def_interceptions, jones.def_interception_yards) == (1, 24)
    assert jones.interceptions_thrown is None

    butker = rows["3055899"]
    assert (butker.field_goals_made, butker.field_goal_attempts) == (1, 1)
    assert (butker.extra_points_made, butker.extra_point_attempts) == (4, 4)
    assert (butker.field_goal_long, butker.kicking_points) == (28, 7)

    remigio = rows["4372716"]  # kick + punt returns
    assert (remigio.kick_returns, remigio.kick_return_yards) == (3, 78)
    assert (remigio.punt_returns, remigio.punt_return_yards) == (2, 7)


def test_player_game_stats_missing_values_become_null(spark):
    from lib.schemas import TABLES
    from lib.transforms import parse_player_game_stats

    raw = spark.createDataFrame(
        [
            {
                "game_id": "1",
                "header": {"season": {"year": 2025, "type": 2}, "week": 3},
                "boxscore": {
                    "players": [
                        {
                            "team": {
                                "id": "12",
                                "abbreviation": "KC",
                                "displayName": "KC",
                            },
                            "statistics": [
                                {
                                    "name": "passing",
                                    "keys": [
                                        "completions/passingAttempts",
                                        "adjQBR",
                                        "QBRating",
                                    ],
                                    # '--' placeholder and a stats array shorter than keys
                                    "athletes": [
                                        {
                                            "athlete": {
                                                "id": "9",
                                                "displayName": "X",
                                                "jersey": "1",
                                            },
                                            "stats": ["3/5", "--"],
                                        },
                                    ],
                                }
                            ],
                        }
                    ]
                },
                "_source_file": "f.json",
                "_snapshot_at": datetime(2025, 9, 1),
            }
        ],
        schema=f"{TABLES['player_game_stats'].raw_schema}, _source_file STRING, _snapshot_at TIMESTAMP",
    )
    row = parse_player_game_stats(raw).first()
    assert (row.pass_completions, row.pass_attempts) == (3, 5)
    assert row.qbr is None
    assert row.passer_rating is None


def test_games_golden_values(read_fixture):
    (game,) = parse(read_fixture, "games").collect()
    assert game.game_id == GAME_ID
    assert (game.season, game.season_type, game.week) == (2026, 2, 1)
    assert game.game_ts_utc == datetime(2026, 9, 15, 0, 15)
    assert (game.status, game.completed) == ("STATUS_FINAL", True)
    assert (game.home_team_id, game.home_score) == ("12", 31)
    assert (game.away_team_id, game.away_score) == ("7", 10)
    assert (game.venue, game.venue_city, game.venue_state) == (
        "Arrowhead Stadium",
        "Kansas City",
        "MO",
    )
    assert game.attendance == 73543


def test_schedules_scores_and_status(read_fixture):
    rows = by_key(parse(read_fixture, "schedules").collect(), "game_id")

    final = rows[GAME_ID]
    assert (final.status, final.completed, final.week, final.season_type) == (
        "STATUS_FINAL",
        True,
        1,
        2,
    )
    assert (final.home_team_abbreviation, final.home_score) == ("KC", 31)
    assert (final.away_team_abbreviation, final.away_score) == ("DEN", 10)
    assert final.game_ts_utc == datetime(2026, 9, 15, 0, 15)

    upcoming = rows["401872976"]
    assert (upcoming.status, upcoming.completed) == ("STATUS_SCHEDULED", False)
    assert upcoming.home_score is None and upcoming.away_score is None
    assert (upcoming.home_team_name, upcoming.away_team_name) == (
        "Las Vegas Raiders",
        "Kansas City Chiefs",
    )
    assert upcoming.venue_city == "Las Vegas"


def test_standings_golden_values(read_fixture):
    rows = by_key(parse(read_fixture, "standings").collect(), "team_id")
    kc = rows["12"]
    assert (kc.season, kc.season_type, kc.conference) == (2026, 2, "AFC")
    assert (kc.wins, kc.losses, kc.ties) == (3, 0, 0)
    assert kc.win_percent == 1.0
    assert (kc.points_for, kc.points_against, kc.point_differential) == (88, 50, 38)
    assert kc.games_behind == 0.0
    assert kc.playoff_seed == 1
    assert kc.streak == "W3"
    assert (kc.division_wins, kc.division_losses) == (1, 0)
    assert (kc.overall_record, kc.home_record, kc.road_record) == ("3-0", "2-0", "1-0")
    assert (kc.division_record, kc.conference_record) == ("1-0", "3-0")
    assert rows["16"].conference == "NFC"


def test_rosters_flatten_groups(read_fixture):
    rows = parse(read_fixture, "rosters").collect()
    assert len(rows) == 10  # fixture keeps 2 players from each of 5+ roster groups
    assert {r.roster_group for r in rows} >= {"offense", "defense", "practiceSquad"}

    allen = by_key(rows, "player_id")["4912218"]
    assert (allen.season, allen.season_type, allen.team_id) == (2026, 2, "12")
    assert (allen.full_name, allen.position, allen.position_name) == (
        "Cyrus Allen",
        "WR",
        "Wide Receiver",
    )
    assert (allen.jersey, allen.age, allen.height_in, allen.weight_lbs) == (
        "13",
        23,
        71,
        180,
    )
    assert (allen.experience_years, allen.college, allen.status) == (
        0,
        "Cincinnati",
        "Active",
    )


def test_players_golden_values(read_fixture):
    rows = by_key(parse(read_fixture, "players").collect(), "player_id")
    kasay = rows["139"]
    assert kasay.full_name == "John Kasay"
    assert (kasay.height_in, kasay.weight_lbs) == (70, 210)
    assert kasay.date_of_birth == date(1969, 10, 27)
    assert (kasay.birth_city, kasay.birth_state) == ("Athens", "GA")
    assert (kasay.experience_years, kasay.hand, kasay.active) == (22, "right", False)
    assert rows["286"].birth_city is None  # birthPlace absent in source


def test_teams_colors_normalized(read_fixture):
    rows = by_key(parse(read_fixture, "teams").collect(), "team_id")
    ari = rows["22"]
    assert (ari.abbreviation, ari.display_name, ari.name) == (
        "ARI",
        "Arizona Cardinals",
        "Cardinals",
    )
    assert (ari.color, ari.alternate_color) == ("#a40227", "#ffffff")
    assert ari.is_active is True


def test_injury_news(read_fixture):
    rows = by_key(parse(read_fixture, "injury_news").collect(), "id")
    assert set(rows) == {611847, 611846, 611845}
    gilliam = rows[611847]
    assert gilliam.created_at == datetime(2026, 10, 2, 16, 5, 28)
    assert (gilliam.player_id, gilliam.team_id, gilliam.sport_id) == (
        19844,
        "NE",
        "NFL",
    )
    assert gilliam.title.startswith("Reggie Gilliam (knee)")
    assert gilliam.description.startswith("Reggie Gilliam returned to practice")
    assert gilliam.categories == ["Commentary", "News", "Injury"]
    assert gilliam.source_file.endswith("injury_news.json")


def test_malformed_stat_values_become_null(spark):
    from lib.transforms import mmss_to_seconds, split_pair, to_long
    from pyspark.sql import functions as F

    df = spark.createDataFrame(
        [("-", "x-y", "bad"), ("", "3-", "12:")], "n STRING, pair STRING, clock STRING"
    )
    made, att = split_pair(F.col("pair"), "-")
    rows = df.select(
        to_long(F.col("n")).alias("n"),
        made.alias("m"),
        att.alias("a"),
        mmss_to_seconds(F.col("clock")).alias("s"),
    ).collect()
    for row in rows:
        assert row.n is None and row.a is None and row.s is None
    assert rows[1].m == 3

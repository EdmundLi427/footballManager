"""Single source of truth for every ESPN/FantasyPros ingest: raw read schemas and silver tables.

- ``*_RAW`` DDL strings are the explicit Auto Loader read schemas for the blobs Azure lands
  (ESPN field names, nesting kept). Only fields a parser reads are declared; extra ESPN fields
  are ignored. Values whose JSON type varies (box score ``value`` is a number or ``'-'``) are
  read as STRING and cast safely in transforms.py.
- ``TABLES`` defines each silver Delta table: column order, types, comments, keys.
  Notebook DDL, parser output and merge keys are all derived from it.

See databricks/TRANSFORMS.md for the column-by-column mapping.
"""

from dataclasses import dataclass

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DataType,
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

SCHEMA = "nfl"

# ---------------------------------------------------------------------------
# Raw (landing blob) read schemas
# ---------------------------------------------------------------------------

_TEAM_REF = "STRUCT<id: STRING, abbreviation: STRING, displayName: STRING>"
_STATUS = "STRUCT<type: STRUCT<name: STRING, state: STRING, completed: BOOLEAN, detail: STRING>>"
_VENUE = "STRUCT<fullName: STRING, address: STRUCT<city: STRING, state: STRING>>"

TEAMS_RAW = (
    "id STRING, uid STRING, slug STRING, abbreviation STRING, location STRING, name STRING, "
    "nickname STRING, displayName STRING, shortDisplayName STRING, color STRING, "
    "alternateColor STRING, isActive BOOLEAN"
)

PLAYERS_RAW = (
    "id STRING, uid STRING, guid STRING, firstName STRING, lastName STRING, fullName STRING, "
    "displayName STRING, shortName STRING, jersey STRING, height DOUBLE, weight DOUBLE, age INT, "
    "dateOfBirth STRING, birthPlace STRUCT<city: STRING, state: STRING, country: STRING>, "
    "experience STRUCT<years: INT>, hand STRUCT<type: STRING>, active BOOLEAN"
)

ROSTERS_RAW = (
    f"season STRUCT<year: INT, type: INT>, team {_TEAM_REF}, "
    "athletes ARRAY<STRUCT<position: STRING, items: ARRAY<STRUCT<"
    "id: STRING, fullName: STRING, jersey: STRING, age: INT, height: DOUBLE, weight: DOUBLE, "
    "experience: STRUCT<years: INT>, college: STRUCT<name: STRING>, "
    "position: STRUCT<abbreviation: STRING, name: STRING>, status: STRUCT<name: STRING>>>>>"
)

STANDINGS_RAW = (
    "children ARRAY<STRUCT<name: STRING, abbreviation: STRING, standings: STRUCT<"
    "season: INT, seasonType: INT, entries: ARRAY<STRUCT<"
    f"team: {_TEAM_REF}, "
    "stats: ARRAY<STRUCT<name: STRING, value: STRING, displayValue: STRING>>>>>>>"
)

SCHEDULES_RAW = (
    "id STRING, date STRING, season STRUCT<year: INT>, seasonType STRUCT<type: INT>, "
    "week STRUCT<number: INT>, competitions ARRAY<STRUCT<"
    f"date: STRING, neutralSite: BOOLEAN, venue: {_VENUE}, status: {_STATUS}, "
    f"competitors: ARRAY<STRUCT<homeAway: STRING, team: {_TEAM_REF}, "
    "score: STRUCT<value: DOUBLE, displayValue: STRING>>>>>"
)

GAME_SUMMARIES_RAW = (
    "game_id STRING, header STRUCT<season: STRUCT<year: INT, type: INT>, week: INT, "
    f"competitions: ARRAY<STRUCT<date: STRING, neutralSite: BOOLEAN, status: {_STATUS}, "
    f"competitors: ARRAY<STRUCT<homeAway: STRING, score: STRING, team: {_TEAM_REF}>>>>>, "
    f"gameInfo STRUCT<venue: {_VENUE}, attendance: BIGINT>, "
    f"boxscore STRUCT<teams: ARRAY<STRUCT<homeAway: STRING, team: {_TEAM_REF}, "
    "statistics: ARRAY<STRUCT<name: STRING, value: STRING, displayValue: STRING>>>>>"
)

# Same game_summaries blobs, read for the per-player box score only. Each stat group carries
# parallel `keys` and per-athlete `stats` arrays of strings ('--' when ESPN has no value).
PLAYER_BOX_RAW = (
    "game_id STRING, header STRUCT<season: STRUCT<year: INT, type: INT>, week: INT>, "
    f"boxscore STRUCT<players: ARRAY<STRUCT<team: {_TEAM_REF}, "
    "statistics: ARRAY<STRUCT<name: STRING, keys: ARRAY<STRING>, athletes: ARRAY<STRUCT<"
    "athlete: STRUCT<id: STRING, displayName: STRING, jersey: STRING>, "
    "stats: ARRAY<STRING>>>>>>>>"
)

INJURY_RAW = (
    "sport STRING, title STRING, description STRING, count BIGINT, items ARRAY<STRUCT<"
    "id: BIGINT, created: STRING, created_formated: STRING, author: STRING, player_id: BIGINT, "
    "team_id: STRING, title: STRING, sport_id: STRING, categories: ARRAY<STRING>, link: STRING, "
    "desc: STRING, impact: STRING>>, limit BIGINT, public_api BOOLEAN, tier STRING"
)

# ---------------------------------------------------------------------------
# Silver tables
# ---------------------------------------------------------------------------


def _col(
    name: str, dtype: DataType, comment: str = "", nullable: bool = True
) -> StructField:
    return StructField(name, dtype, nullable, {"comment": comment} if comment else {})


S, L, D, B, TS = StringType(), LongType(), DoubleType(), BooleanType(), TimestampType()

AUDIT_COLUMNS = (
    _col("_source_file", S, "Blob path the row was read from"),
    _col("_snapshot_at", TS, "Blob modification time; newer snapshots win merges"),
    _col("_ingested_at", TS, "When the row was written to Delta"),
)


@dataclass(frozen=True)
class Table:
    name: str
    source_prefix: str
    raw_schema: str
    schema: StructType
    keys: tuple
    snapshot_col: str = "_snapshot_at"
    description: str = ""

    @property
    def full_name(self) -> str:
        return f"{SCHEMA}.{self.name}"

    @property
    def columns(self) -> list:
        return [f.name for f in self.schema.fields]


def _schema(*fields: StructField) -> StructType:
    return StructType(list(fields) + list(AUDIT_COLUMNS))


TEAMS = Table(
    name="teams",
    source_prefix="espn/teams",
    raw_schema=TEAMS_RAW,
    keys=("team_id",),
    description="One row per NFL franchise.",
    schema=_schema(
        _col("team_id", S, "ESPN team id", nullable=False),
        _col("uid", S, "ESPN universal id"),
        _col("slug", S),
        _col("abbreviation", S, "e.g. KC"),
        _col("location", S, "e.g. Kansas City"),
        _col("name", S, "e.g. Chiefs"),
        _col("display_name", S, "e.g. Kansas City Chiefs"),
        _col("short_display_name", S),
        _col("color", S, "Primary color, '#rrggbb'"),
        _col("alternate_color", S, "Secondary color, '#rrggbb'"),
        _col("is_active", B),
    ),
)

PLAYERS = Table(
    name="players",
    source_prefix="espn/players",
    raw_schema=PLAYERS_RAW,
    keys=("player_id",),
    description="One row per athlete in ESPN's season athlete index (includes inactive/retired).",
    schema=_schema(
        _col("player_id", S, "ESPN athlete id", nullable=False),
        _col("uid", S),
        _col("guid", S),
        _col("first_name", S),
        _col("last_name", S),
        _col("full_name", S),
        _col("display_name", S),
        _col("short_name", S),
        _col("jersey", S, "Jersey number as printed (may be null)"),
        _col("height_in", L, "Height in inches"),
        _col("weight_lbs", L, "Weight in pounds"),
        _col("age", L),
        _col("date_of_birth", DateType()),
        _col("birth_city", S),
        _col("birth_state", S),
        _col("birth_country", S),
        _col("experience_years", L),
        _col("hand", S, "Lower-case handedness, e.g. right"),
        _col("active", B),
    ),
)

ROSTERS = Table(
    name="rosters",
    source_prefix="espn/rosters",
    raw_schema=ROSTERS_RAW,
    keys=("season", "team_id", "player_id"),
    description=(
        "One row per (season, team, player) ever seen on a roster snapshot. Rows are not deleted "
        "when a player leaves; filter to the latest _snapshot_at per team for the current roster."
    ),
    schema=_schema(
        _col("season", L, "Season year", nullable=False),
        _col("season_type", L, "1=pre, 2=regular, 3=post"),
        _col("team_id", S, nullable=False),
        _col("team_abbreviation", S),
        _col("player_id", S, nullable=False),
        _col("full_name", S),
        _col(
            "roster_group",
            S,
            "offense, defense, specialTeam, injuredReserveOrOut, practiceSquad, ...",
        ),
        _col("position", S, "Position abbreviation, e.g. WR"),
        _col("position_name", S, "e.g. Wide Receiver"),
        _col("jersey", S),
        _col("age", L),
        _col("height_in", L),
        _col("weight_lbs", L),
        _col("experience_years", L),
        _col("college", S),
        _col("status", S, "e.g. Active"),
    ),
)

STANDINGS = Table(
    name="standings",
    source_prefix="espn/standings",
    raw_schema=STANDINGS_RAW,
    keys=("season", "season_type", "team_id"),
    description="One row per team per season type; latest snapshot wins.",
    schema=_schema(
        _col("season", L, nullable=False),
        _col("season_type", L, "1=pre, 2=regular, 3=post", nullable=False),
        _col("conference", S, "AFC or NFC"),
        _col("team_id", S, nullable=False),
        _col("team_abbreviation", S),
        _col("team_name", S),
        _col("wins", L),
        _col("losses", L),
        _col("ties", L),
        _col("win_percent", D),
        _col("points_for", L),
        _col("points_against", L),
        _col("point_differential", L),
        _col("games_behind", D),
        _col("playoff_seed", L),
        _col("streak", S, "e.g. W3, L1"),
        _col("division_wins", L),
        _col("division_losses", L),
        _col("division_ties", L),
        _col("overall_record", S, "W-L[-T]"),
        _col("home_record", S),
        _col("road_record", S),
        _col("division_record", S),
        _col("conference_record", S),
    ),
)

_GAME_CORE = (
    _col("game_id", S, "ESPN event id", nullable=False),
    _col("season", L),
    _col(
        "season_type", L, "1=pre, 2=regular, 3=post; week numbers repeat across types"
    ),
    _col("week", L),
    _col("game_ts_utc", TS, "Kickoff time (UTC)"),
    _col("status", S, "ESPN status name, e.g. STATUS_FINAL, STATUS_SCHEDULED"),
    _col("status_detail", S, "Human-readable status, e.g. Final/OT"),
    _col("completed", B),
)

SCHEDULES = Table(
    name="schedules",
    source_prefix="espn/schedules",
    raw_schema=SCHEDULES_RAW,
    keys=("game_id",),
    description="One row per scheduled game; scores null until kickoff.",
    schema=_schema(
        *_GAME_CORE,
        _col("home_team_id", S),
        _col("home_team_abbreviation", S),
        _col("home_team_name", S),
        _col("home_score", L, "Null before kickoff; live during the game"),
        _col("away_team_id", S),
        _col("away_team_abbreviation", S),
        _col("away_team_name", S),
        _col("away_score", L, "Null before kickoff; live during the game"),
        _col("venue", S),
        _col("venue_city", S),
        _col("venue_state", S),
        _col("neutral_site", B),
    ),
)

GAMES = Table(
    name="games",
    source_prefix="espn/game_summaries",
    raw_schema=GAME_SUMMARIES_RAW,
    keys=("game_id",),
    description="One row per game that has kicked off (from game summaries).",
    schema=_schema(
        *_GAME_CORE,
        _col("home_team_id", S),
        _col("home_score", L),
        _col("away_team_id", S),
        _col("away_score", L),
        _col("venue", S),
        _col("venue_city", S),
        _col("venue_state", S),
        _col("neutral_site", B),
        _col("attendance", L),
    ),
)

GAME_TEAM_STATS = Table(
    name="game_team_stats",
    source_prefix="espn/game_summaries",
    raw_schema=GAME_SUMMARIES_RAW,
    keys=("game_id", "team_id"),
    description="One row per team per game box score.",
    schema=_schema(
        _col("game_id", S, nullable=False),
        _col("team_id", S, nullable=False),
        _col("team_abbreviation", S),
        _col("home_away", S, "home or away"),
        _col("first_downs", L),
        _col("first_downs_passing", L),
        _col("first_downs_rushing", L),
        _col("first_downs_penalty", L),
        _col("third_down_conversions", L),
        _col("third_down_attempts", L),
        _col("fourth_down_conversions", L),
        _col("fourth_down_attempts", L),
        _col("total_offensive_plays", L),
        _col("total_yards", L),
        _col("yards_per_play", D),
        _col("total_drives", L),
        _col("net_passing_yards", L),
        _col("pass_completions", L),
        _col("pass_attempts", L),
        _col("yards_per_pass", D),
        _col("interceptions_thrown", L),
        _col("sacks", L, "Times sacked"),
        _col("sack_yards_lost", L),
        _col("rushing_yards", L),
        _col("rushing_attempts", L),
        _col("yards_per_rush_attempt", D),
        _col("red_zone_scores", L),
        _col("red_zone_attempts", L),
        _col("penalties", L),
        _col("penalty_yards", L),
        _col("turnovers", L),
        _col("fumbles_lost", L),
        _col("defensive_touchdowns", L, "Defensive / special teams TDs"),
        _col("possession_seconds", L),
    ),
)

PLAYER_GAME_STATS = Table(
    name="player_game_stats",
    source_prefix="espn/game_summaries",
    raw_schema=PLAYER_BOX_RAW,
    keys=("game_id", "player_id"),
    description=(
        "One row per player per game box score. Stats a player did not record are NULL. "
        "No position or fantasy points: join rosters for position, compute scoring in SQL."
    ),
    schema=_schema(
        _col("game_id", S, nullable=False),
        _col("player_id", S, "ESPN athlete id", nullable=False),
        _col("player_name", S),
        _col("jersey", S),
        _col("team_id", S),
        _col("team_abbreviation", S),
        _col("season", L),
        _col("season_type", L, "1=pre, 2=regular, 3=post"),
        _col("week", L),
        _col("pass_completions", L),
        _col("pass_attempts", L),
        _col("passing_yards", L),
        _col("passing_tds", L),
        _col("interceptions_thrown", L),
        _col("sacks_taken", L),
        _col("sack_yards_lost", L),
        _col("passer_rating", D, "NFL passer rating"),
        _col("qbr", D, "ESPN adjusted QBR"),
        _col("rushing_attempts", L),
        _col("rushing_yards", L),
        _col("rushing_tds", L),
        _col("rushing_long", L),
        _col("receptions", L),
        _col("targets", L),
        _col("receiving_yards", L),
        _col("receiving_tds", L),
        _col("receiving_long", L),
        _col("fumbles", L),
        _col("fumbles_lost", L),
        _col("fumbles_recovered", L),
        _col("tackles_total", L),
        _col("tackles_solo", L),
        _col("def_sacks", D, "Half sacks possible"),
        _col("tackles_for_loss", D),
        _col("passes_defended", L),
        _col("qb_hits", L),
        _col("def_tds", L, "Defensive touchdowns"),
        _col("def_interceptions", L),
        _col("def_interception_yards", L),
        _col("def_interception_tds", L),
        _col("kick_returns", L),
        _col("kick_return_yards", L),
        _col("kick_return_tds", L),
        _col("punt_returns", L),
        _col("punt_return_yards", L),
        _col("punt_return_tds", L),
        _col("field_goals_made", L),
        _col("field_goal_attempts", L),
        _col("field_goal_long", L),
        _col("extra_points_made", L),
        _col("extra_point_attempts", L),
        _col("kicking_points", L),
        _col("punts", L),
        _col("punt_yards", L),
        _col("punts_inside_20", L),
    ),
)

INJURY_NEWS = Table(
    name="injury_news",
    source_prefix="injury",
    raw_schema=INJURY_RAW,
    keys=("id",),
    snapshot_col="snapshot_at",
    description="FantasyPros injury news; one row per article, newest snapshot wins.",
    schema=StructType(
        [
            _col("id", L, "FantasyPros news ID (primary key)", nullable=False),
            _col("player_id", L),
            _col("team_id", S),
            _col("title", S),
            _col("description", S),
            _col("impact", S),
            _col("author", S),
            _col("categories", ArrayType(S)),
            _col("link", S),
            _col("sport_id", S),
            _col("created_at", TS, "When FantasyPros published it (UTC)"),
            _col(
                "snapshot_at",
                TS,
                "When the snapshot file containing this version was written",
            ),
            _col("source_file", S),
            _col("ingested_at", TS),
        ]
    ),
)

TABLES = {
    t.name: t
    for t in (
        TEAMS,
        PLAYERS,
        ROSTERS,
        STANDINGS,
        SCHEDULES,
        GAMES,
        GAME_TEAM_STATS,
        PLAYER_GAME_STATS,
        INJURY_NEWS,
    )
}

ESPN_TABLES = tuple(name for name in TABLES if name != "injury_news")

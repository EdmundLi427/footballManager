"""Pure DataFrame -> DataFrame parsers from landing blobs to silver tables.

Every ``parse_*`` takes a DataFrame read with the table's ``raw_schema`` plus the audit
columns ``_source_file`` and ``_snapshot_at`` (see ``with_file_metadata``), and returns
exactly ``TABLES[name].schema`` (column order and types). No I/O happens here, so the
parsers run identically in notebooks, streaming ``foreachBatch`` and local tests.

Casts go through ``to_double``/``to_long`` so malformed ESPN values (``'-'``, ``''``)
become NULL instead of failing under ANSI mode. Works on Spark 3.5 and 4.x.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from .schemas import TABLES, Table

_NUMERIC = r"^\s*-?\d+(\.\d+)?([eE][-+]?\d+)?\s*$"
_ESPN_TS_FORMATS = ("yyyy-MM-dd'T'HH:mmX", "yyyy-MM-dd'T'HH:mm:ssX")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def to_double(c: Column) -> Column:
    s = c.cast("string")
    return F.when(s.rlike(_NUMERIC), F.trim(s).cast("double"))


def to_long(c: Column) -> Column:
    return to_double(c).cast("bigint")


def espn_timestamp(c: Column) -> Column:
    return F.coalesce(*[F.try_to_timestamp(c, F.lit(fmt)) for fmt in _ESPN_TS_FORMATS])


def first_where(arr: Column, predicate) -> Column:
    """First element of ``arr`` matching ``predicate``, or NULL."""
    return F.try_element_at(F.filter(arr, predicate), F.lit(1))


def stat(stats: Column, name: str, field: str = "displayValue") -> Column:
    """Field of the first ``{name, value, displayValue}`` entry called ``name``."""
    return first_where(stats, lambda s: s["name"] == name)[field]


def split_pair(c: Column, sep: str) -> tuple:
    """'9-17' -> (9, 17); separator is a literal character."""
    parts = F.split(c, "\\" + sep)
    return (
        to_long(F.try_element_at(parts, F.lit(1))),
        to_long(F.try_element_at(parts, F.lit(2))),
    )


def mmss_to_seconds(c: Column) -> Column:
    parts = F.split(c, ":")
    minutes = to_long(F.try_element_at(parts, F.lit(1)))
    seconds = to_long(F.try_element_at(parts, F.lit(2)))
    return minutes * 60 + seconds


def hex_color(c: Column) -> Column:
    return F.when(F.length(c) > 0, F.concat(F.lit("#"), F.lower(c)))


def with_file_metadata(df: DataFrame) -> DataFrame:
    """Adds ``_source_file``/``_snapshot_at`` from Auto Loader's hidden ``_metadata`` column."""
    return df.withColumn("_source_file", F.col("_metadata.file_path")).withColumn(
        "_snapshot_at", F.col("_metadata.file_modification_time")
    )


def _finish(df: DataFrame, table: Table) -> DataFrame:
    """Adds ``_ingested_at`` and selects/casts to exactly the table schema."""
    df = df.withColumn("_ingested_at", F.current_timestamp())
    return df.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in table.schema.fields]
    )


def _competitor(competitors: Column, side: str) -> Column:
    return first_where(competitors, lambda c: c["homeAway"] == side)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_teams(df: DataFrame) -> DataFrame:
    return _finish(
        df.select(
            F.col("id").alias("team_id"),
            "uid",
            "slug",
            "abbreviation",
            "location",
            "name",
            F.col("displayName").alias("display_name"),
            F.col("shortDisplayName").alias("short_display_name"),
            hex_color(F.col("color")).alias("color"),
            hex_color(F.col("alternateColor")).alias("alternate_color"),
            F.col("isActive").alias("is_active"),
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["teams"],
    )


def parse_players(df: DataFrame) -> DataFrame:
    return _finish(
        df.select(
            F.col("id").alias("player_id"),
            "uid",
            "guid",
            F.col("firstName").alias("first_name"),
            F.col("lastName").alias("last_name"),
            F.col("fullName").alias("full_name"),
            F.col("displayName").alias("display_name"),
            F.col("shortName").alias("short_name"),
            "jersey",
            to_long(F.col("height")).alias("height_in"),
            to_long(F.col("weight")).alias("weight_lbs"),
            F.col("age"),
            F.try_to_timestamp(F.substring("dateOfBirth", 1, 10), F.lit("yyyy-MM-dd"))
            .cast("date")
            .alias("date_of_birth"),
            F.col("birthPlace.city").alias("birth_city"),
            F.col("birthPlace.state").alias("birth_state"),
            F.col("birthPlace.country").alias("birth_country"),
            F.col("experience.years").alias("experience_years"),
            F.lower("hand.type").alias("hand"),
            "active",
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["players"],
    )


def parse_rosters(df: DataFrame) -> DataFrame:
    players = df.select(
        "season",
        "team",
        F.explode("athletes").alias("grp"),
        "_source_file",
        "_snapshot_at",
    ).select("*", F.explode("grp.items").alias("p"))
    return _finish(
        players.select(
            F.col("season.year").alias("season"),
            F.col("season.type").alias("season_type"),
            F.col("team.id").alias("team_id"),
            F.col("team.abbreviation").alias("team_abbreviation"),
            F.col("p.id").alias("player_id"),
            F.col("p.fullName").alias("full_name"),
            F.col("grp.position").alias("roster_group"),
            F.col("p.position.abbreviation").alias("position"),
            F.col("p.position.name").alias("position_name"),
            F.col("p.jersey").alias("jersey"),
            F.col("p.age").alias("age"),
            to_long(F.col("p.height")).alias("height_in"),
            to_long(F.col("p.weight")).alias("weight_lbs"),
            F.col("p.experience.years").alias("experience_years"),
            F.col("p.college.name").alias("college"),
            F.col("p.status.name").alias("status"),
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["rosters"],
    )


def parse_standings(df: DataFrame) -> DataFrame:
    entries = df.select(
        F.explode("children").alias("conf"), "_source_file", "_snapshot_at"
    ).select("*", F.explode("conf.standings.entries").alias("e"))
    stats = F.col("e.stats")

    def num(name):
        return stat(stats, name, "value")

    def text(name):
        return stat(stats, name, "displayValue")

    return _finish(
        entries.select(
            F.col("conf.standings.season").alias("season"),
            F.col("conf.standings.seasonType").alias("season_type"),
            F.col("conf.abbreviation").alias("conference"),
            F.col("e.team.id").alias("team_id"),
            F.col("e.team.abbreviation").alias("team_abbreviation"),
            F.col("e.team.displayName").alias("team_name"),
            to_long(num("wins")).alias("wins"),
            to_long(num("losses")).alias("losses"),
            to_long(num("ties")).alias("ties"),
            to_double(num("winPercent")).alias("win_percent"),
            to_long(num("pointsFor")).alias("points_for"),
            to_long(num("pointsAgainst")).alias("points_against"),
            to_long(num("pointDifferential")).alias("point_differential"),
            to_double(num("gamesBehind")).alias("games_behind"),
            to_long(num("playoffSeed")).alias("playoff_seed"),
            text("streak").alias("streak"),
            to_long(num("divisionWins")).alias("division_wins"),
            to_long(num("divisionLosses")).alias("division_losses"),
            to_long(num("divisionTies")).alias("division_ties"),
            text("overall").alias("overall_record"),
            text("Home").alias("home_record"),
            text("Road").alias("road_record"),
            text("vs. Div.").alias("division_record"),
            text("vs. Conf.").alias("conference_record"),
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["standings"],
    )


def parse_schedules(df: DataFrame) -> DataFrame:
    comp = F.try_element_at(F.col("competitions"), F.lit(1))
    home = _competitor(comp["competitors"], "home")
    away = _competitor(comp["competitors"], "away")
    started = comp["status"]["type"]["state"] != F.lit("pre")

    def score(competitor):
        return F.when(started, to_long(competitor["score"]["value"]))

    return _finish(
        df.select(
            F.col("id").alias("game_id"),
            F.col("season.year").alias("season"),
            F.col("seasonType.type").alias("season_type"),
            F.col("week.number").alias("week"),
            espn_timestamp(F.coalesce(comp["date"], F.col("date"))).alias(
                "game_ts_utc"
            ),
            comp["status"]["type"]["name"].alias("status"),
            comp["status"]["type"]["detail"].alias("status_detail"),
            comp["status"]["type"]["completed"].alias("completed"),
            home["team"]["id"].alias("home_team_id"),
            home["team"]["abbreviation"].alias("home_team_abbreviation"),
            home["team"]["displayName"].alias("home_team_name"),
            score(home).alias("home_score"),
            away["team"]["id"].alias("away_team_id"),
            away["team"]["abbreviation"].alias("away_team_abbreviation"),
            away["team"]["displayName"].alias("away_team_name"),
            score(away).alias("away_score"),
            comp["venue"]["fullName"].alias("venue"),
            comp["venue"]["address"]["city"].alias("venue_city"),
            comp["venue"]["address"]["state"].alias("venue_state"),
            comp["neutralSite"].alias("neutral_site"),
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["schedules"],
    )


def parse_games(df: DataFrame) -> DataFrame:
    comp = F.try_element_at(F.col("header.competitions"), F.lit(1))
    home = _competitor(comp["competitors"], "home")
    away = _competitor(comp["competitors"], "away")
    venue = F.col("gameInfo.venue")

    return _finish(
        df.select(
            "game_id",
            F.col("header.season.year").alias("season"),
            F.col("header.season.type").alias("season_type"),
            F.col("header.week").alias("week"),
            espn_timestamp(comp["date"]).alias("game_ts_utc"),
            comp["status"]["type"]["name"].alias("status"),
            comp["status"]["type"]["detail"].alias("status_detail"),
            comp["status"]["type"]["completed"].alias("completed"),
            home["team"]["id"].alias("home_team_id"),
            to_long(home["score"]).alias("home_score"),
            away["team"]["id"].alias("away_team_id"),
            to_long(away["score"]).alias("away_score"),
            venue["fullName"].alias("venue"),
            venue["address"]["city"].alias("venue_city"),
            venue["address"]["state"].alias("venue_state"),
            comp["neutralSite"].alias("neutral_site"),
            F.col("gameInfo.attendance").alias("attendance"),
            "_source_file",
            "_snapshot_at",
        ),
        TABLES["games"],
    )


# Single-value box score stats: silver column -> ESPN stat name.
_BOX_LONGS = {
    "first_downs": "firstDowns",
    "first_downs_passing": "firstDownsPassing",
    "first_downs_rushing": "firstDownsRushing",
    "first_downs_penalty": "firstDownsPenalty",
    "total_offensive_plays": "totalOffensivePlays",
    "total_yards": "totalYards",
    "total_drives": "totalDrives",
    "net_passing_yards": "netPassingYards",
    "interceptions_thrown": "interceptions",
    "rushing_yards": "rushingYards",
    "rushing_attempts": "rushingAttempts",
    "turnovers": "turnovers",
    "fumbles_lost": "fumblesLost",
    "defensive_touchdowns": "defensiveTouchdowns",
}
# Rates: ESPN `value` is full precision, `displayValue` is rounded to one decimal.
_BOX_RATES = {
    "yards_per_play": "yardsPerPlay",
    "yards_per_pass": "yardsPerPass",
    "yards_per_rush_attempt": "yardsPerRushAttempt",
}
# "made-att" style stats: ESPN stat name -> (separator, left column, right column).
_BOX_PAIRS = {
    "thirdDownEff": ("-", "third_down_conversions", "third_down_attempts"),
    "fourthDownEff": ("-", "fourth_down_conversions", "fourth_down_attempts"),
    "completionAttempts": ("/", "pass_completions", "pass_attempts"),
    "sacksYardsLost": ("-", "sacks", "sack_yards_lost"),
    "redZoneAttempts": ("-", "red_zone_scores", "red_zone_attempts"),
    "totalPenaltiesYards": ("-", "penalties", "penalty_yards"),
}


def parse_game_team_stats(df: DataFrame) -> DataFrame:
    teams = df.select(
        "game_id",
        F.explode("boxscore.teams").alias("t"),
        "_source_file",
        "_snapshot_at",
    )
    stats = F.col("t.statistics")

    cols = [
        "game_id",
        F.col("t.team.id").alias("team_id"),
        F.col("t.team.abbreviation").alias("team_abbreviation"),
        F.col("t.homeAway").alias("home_away"),
        mmss_to_seconds(stat(stats, "possessionTime")).alias("possession_seconds"),
        "_source_file",
        "_snapshot_at",
    ]
    cols += [
        to_long(stat(stats, espn)).alias(name) for name, espn in _BOX_LONGS.items()
    ]
    cols += [
        to_double(stat(stats, espn, "value")).alias(name)
        for name, espn in _BOX_RATES.items()
    ]
    for espn, (sep, left, right) in _BOX_PAIRS.items():
        made, att = split_pair(stat(stats, espn), sep)
        cols += [made.alias(left), att.alias(right)]

    return _finish(teams.select(*cols), TABLES["game_team_stats"])


# Player box score stats: silver column -> (ESPN stat group, key). Keys repeat across groups
# ('interceptions' is thrown in passing, caught in interceptions), so lookups are per group.
_PLAYER_LONGS = {
    "passing_yards": ("passing", "passingYards"),
    "passing_tds": ("passing", "passingTouchdowns"),
    "interceptions_thrown": ("passing", "interceptions"),
    "rushing_attempts": ("rushing", "rushingAttempts"),
    "rushing_yards": ("rushing", "rushingYards"),
    "rushing_tds": ("rushing", "rushingTouchdowns"),
    "rushing_long": ("rushing", "longRushing"),
    "receptions": ("receiving", "receptions"),
    "targets": ("receiving", "receivingTargets"),
    "receiving_yards": ("receiving", "receivingYards"),
    "receiving_tds": ("receiving", "receivingTouchdowns"),
    "receiving_long": ("receiving", "longReception"),
    "fumbles": ("fumbles", "fumbles"),
    "fumbles_lost": ("fumbles", "fumblesLost"),
    "fumbles_recovered": ("fumbles", "fumblesRecovered"),
    "tackles_total": ("defensive", "totalTackles"),
    "tackles_solo": ("defensive", "soloTackles"),
    "passes_defended": ("defensive", "passesDefended"),
    "qb_hits": ("defensive", "QBHits"),
    "def_tds": ("defensive", "defensiveTouchdowns"),
    "def_interceptions": ("interceptions", "interceptions"),
    "def_interception_yards": ("interceptions", "interceptionYards"),
    "def_interception_tds": ("interceptions", "interceptionTouchdowns"),
    "kick_returns": ("kickReturns", "kickReturns"),
    "kick_return_yards": ("kickReturns", "kickReturnYards"),
    "kick_return_tds": ("kickReturns", "kickReturnTouchdowns"),
    "punt_returns": ("puntReturns", "puntReturns"),
    "punt_return_yards": ("puntReturns", "puntReturnYards"),
    "punt_return_tds": ("puntReturns", "puntReturnTouchdowns"),
    "field_goal_long": ("kicking", "longFieldGoalMade"),
    "kicking_points": ("kicking", "totalKickingPoints"),
    "punts": ("punting", "punts"),
    "punt_yards": ("punting", "puntYards"),
    "punts_inside_20": ("punting", "puntsInside20"),
}
_PLAYER_DOUBLES = {
    "passer_rating": ("passing", "QBRating"),
    "qbr": ("passing", "adjQBR"),
    "def_sacks": ("defensive", "sacks"),
    "tackles_for_loss": ("defensive", "tacklesForLoss"),
}
# "made/att" style stats: (group, key) -> (separator, left column, right column).
_PLAYER_PAIRS = {
    ("passing", "completions/passingAttempts"): (
        "/",
        "pass_completions",
        "pass_attempts",
    ),
    ("passing", "sacks-sackYardsLost"): ("-", "sacks_taken", "sack_yards_lost"),
    ("kicking", "fieldGoalsMade/fieldGoalAttempts"): (
        "/",
        "field_goals_made",
        "field_goal_attempts",
    ),
    ("kicking", "extraPointsMade/extraPointAttempts"): (
        "/",
        "extra_points_made",
        "extra_point_attempts",
    ),
}


def _player_stat(entries: Column, group: str, key: str) -> Column:
    """Raw string value of ``key`` from the player's row in stat ``group``, or NULL."""
    row = first_where(entries, lambda e: e["group"] == group)
    pos = F.array_position(row["keys"], key).cast("int")
    return F.when(pos > 0, F.try_element_at(row["stats"], pos))


def parse_player_game_stats(df: DataFrame) -> DataFrame:
    # Pivot without groupBy (the parser runs on a stream): flatten each team's groups into
    # (group, keys, athlete, stats) entries, explode the distinct athlete ids, then pick that
    # athlete's entry per group.
    teams = df.select(
        "game_id",
        F.col("header.season.year").alias("season"),
        F.col("header.season.type").alias("season_type"),
        F.col("header.week").alias("week"),
        F.explode("boxscore.players").alias("t"),
        "_source_file",
        "_snapshot_at",
    )
    entries = F.flatten(
        F.transform(
            "t.statistics",
            lambda g: F.transform(
                g["athletes"],
                lambda a: F.struct(
                    g["name"].alias("group"),
                    g["keys"].alias("keys"),
                    a["athlete"].alias("athlete"),
                    a["stats"].alias("stats"),
                ),
            ),
        )
    )
    players = teams.withColumn("entries", entries).withColumn(
        "player_id",
        F.explode(
            F.array_distinct(F.transform("entries", lambda e: e["athlete"]["id"]))
        ),
    )
    mine = F.filter("entries", lambda e: e["athlete"]["id"] == F.col("player_id"))
    athlete = F.try_element_at(mine, F.lit(1))["athlete"]

    cols = [
        "game_id",
        "player_id",
        athlete["displayName"].alias("player_name"),
        athlete["jersey"].alias("jersey"),
        F.col("t.team.id").alias("team_id"),
        F.col("t.team.abbreviation").alias("team_abbreviation"),
        "season",
        "season_type",
        "week",
        "_source_file",
        "_snapshot_at",
    ]
    cols += [
        to_long(_player_stat(mine, *src)).alias(name)
        for name, src in _PLAYER_LONGS.items()
    ]
    cols += [
        to_double(_player_stat(mine, *src)).alias(name)
        for name, src in _PLAYER_DOUBLES.items()
    ]
    for src, (sep, left, right) in _PLAYER_PAIRS.items():
        made, att = split_pair(_player_stat(mine, *src), sep)
        cols += [made.alias(left), att.alias(right)]

    return _finish(players.select(*cols), TABLES["player_game_stats"])


def parse_injury_news(df: DataFrame) -> DataFrame:
    """FantasyPros snapshot -> one row per article. Uses the table's legacy audit column names."""
    articles = df.select(
        F.explode("items").alias("a"),
        F.col("_source_file").alias("source_file"),
        F.col("_snapshot_at").alias("snapshot_at"),
    )
    out = articles.select(
        F.col("a.id").alias("id"),
        F.col("a.player_id").alias("player_id"),
        F.col("a.team_id").alias("team_id"),
        F.trim("a.title").alias("title"),
        F.trim("a.desc").alias("description"),
        F.trim("a.impact").alias("impact"),
        F.col("a.author").alias("author"),
        F.col("a.categories").alias("categories"),
        F.col("a.link").alias("link"),
        F.col("a.sport_id").alias("sport_id"),
        F.try_to_timestamp(F.col("a.created"), F.lit("yyyy-MM-dd HH:mm:ss")).alias(
            "created_at"
        ),
        "snapshot_at",
        "source_file",
        F.current_timestamp().alias("ingested_at"),
    )
    schema = TABLES["injury_news"].schema
    return out.select(
        *[F.col(f.name).cast(f.dataType).alias(f.name) for f in schema.fields]
    )


PARSERS = {
    "teams": parse_teams,
    "players": parse_players,
    "rosters": parse_rosters,
    "standings": parse_standings,
    "schedules": parse_schedules,
    "games": parse_games,
    "game_team_stats": parse_game_team_stats,
    "player_game_stats": parse_player_game_stats,
    "injury_news": parse_injury_news,
}

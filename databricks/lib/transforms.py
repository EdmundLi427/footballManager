"""
Shared PySpark transforms for Databricks ingestion notebooks.

Contains schema definitions and pure transform logic extracted from the notebooks,
enabling unit testing and code reuse across multiple ingest jobs.
"""

import re
from pyspark.sql import DataFrame, Window
from pyspark.sql.functions import (
    col,
    row_number,
    current_timestamp,
    to_timestamp,
    trim,
    struct,
    to_json,
    from_json,
    map_filter,
    expr,
)
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    BooleanType,
    IntegerType,
    ArrayType,
    MapType,
    TimestampType,
)

# ============================================================================
# Pure Python utilities (no Spark)
# ============================================================================


def to_snake(column_name: str) -> str:
    """
    Convert a column name to snake_case.

    Handles:
    - camelCase -> camel_case
    - spaces and symbols -> underscores
    - consecutive underscores -> single underscore

    Args:
        column_name: The column name to convert

    Returns:
        The snake_case version
    """
    # Replace non-alphanumeric with underscore
    name = re.sub(r"[^0-9a-zA-Z]+", "_", column_name)
    # Insert underscore before uppercase letters following lowercase/digits
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    # Collapse consecutive underscores, strip leading/trailing, and lowercase
    return re.sub(r"_+", "_", name).strip("_").lower()


def table_name_from_file(filename: str) -> str:
    """
    Derive a table name from a filename.

    Examples:
        game_team_stats.csv -> game_team_stats
        players.json -> players

    Args:
        filename: The source filename

    Returns:
        The derived table name (snake_case, no extension)
    """
    name = filename.rsplit(".", 1)[0]  # Remove extension
    return to_snake(name)


def is_csv(filename: str) -> bool:
    """Check if a filename is a CSV file (case-insensitive)."""
    return filename.lower().endswith(".csv")


# ============================================================================
# Table key mapping (composite keys for upsert)
# ============================================================================

TABLE_KEYS = {
    "injury_news": ["id"],
    "games": ["id"],
    "teams": ["id"],
    "players": ["id"],
    "rosters": ["teamId", "playerId"],
    "schedules": ["id"],
    "standings": ["teamId", "season"],
    "game_team_stats": ["gameId", "teamId"],
}


# ============================================================================
# Schema builders
# ============================================================================


def injury_file_schema() -> StructType:
    """Schema for the FantasyPros injury news JSON response."""
    item_schema = StructType(
        [
            StructField("id", LongType()),
            StructField("created", StringType()),
            StructField("created_formated", StringType()),
            StructField("author", StringType()),
            StructField("player_id", LongType()),
            StructField("team_id", StringType()),
            StructField("title", StringType()),
            StructField("sport_id", StringType()),
            StructField("categories", ArrayType(StringType())),
            StructField("link", StringType()),
            StructField("desc", StringType()),
            StructField("impact", StringType()),
        ]
    )

    return StructType(
        [
            StructField("sport", StringType()),
            StructField("title", StringType()),
            StructField("description", StringType()),
            StructField("count", LongType()),
            StructField("items", ArrayType(item_schema)),
            StructField("limit", LongType()),
            StructField("public_api_limited", BooleanType()),
            StructField("tier", StringType()),
        ]
    )


def games_schema() -> StructType:
    """Schema for ESPN games data (cleaned schema from Azure pluckGameHeader)."""
    return StructType(
        [
            StructField("game_id", StringType()),
            StructField("date", StringType()),
            StructField("status", StringType()),
            StructField("completed", BooleanType()),
            StructField("venue", StringType()),
            StructField("city", StringType()),
        ]
    )


def teams_schema() -> StructType:
    """Schema for ESPN teams data."""
    return StructType(
        [
            StructField("id", StringType()),
            StructField("uid", StringType()),
            StructField("slug", StringType()),
            StructField("abbreviation", StringType()),
            StructField("displayName", StringType()),
            StructField("isActive", BooleanType()),
            StructField("color", StringType()),
            StructField("alternateColor", StringType()),
        ]
    )


def players_schema() -> StructType:
    """Schema for ESPN players data."""
    return StructType(
        [
            StructField("id", StringType()),
            StructField("uid", StringType()),
            StructField("guid", StringType()),
            StructField("name", StringType()),
            StructField("dateOfBirth", StringType()),
            StructField("birthplace", StringType()),
            StructField("hand", StringType()),
            StructField("height", StringType()),
            StructField("weight", StringType()),
            StructField("experience", StringType()),
        ]
    )


def rosters_schema() -> StructType:
    """Schema for ESPN rosters data."""
    return StructType(
        [
            StructField("teamId", StringType()),
            StructField("position", StringType()),
            StructField("playerId", StringType()),
            StructField("playerName", StringType()),
            StructField("jersey", StringType()),
            StructField("age", IntegerType()),
            StructField("height", StringType()),
            StructField("weight", StringType()),
            StructField("experience", StringType()),
        ]
    )


def schedules_schema() -> StructType:
    """Schema for ESPN schedules data (cleaned schema from Azure pluckScheduleFields)."""
    return StructType(
        [
            StructField("season", IntegerType()),
            StructField("game_id", StringType()),
            StructField("date", StringType()),
            StructField("week", IntegerType()),
            StructField("status", StringType()),
            StructField("completed", BooleanType()),
            StructField("home_team_id", StringType()),
            StructField("home_team", StringType()),
            StructField("home_score", IntegerType()),
            StructField("away_team_id", StringType()),
            StructField("away_team", StringType()),
            StructField("away_score", IntegerType()),
            StructField("venue", StringType()),
        ]
    )


# ============================================================================
# DataFrame transforms
# ============================================================================


def snake_case_columns(df: DataFrame) -> DataFrame:
    """
    Rename all DataFrame columns to snake_case.

    Args:
        df: Input DataFrame

    Returns:
        DataFrame with snake_case column names
    """
    for col_name in df.columns:
        df = df.withColumnRenamed(col_name, to_snake(col_name))
    return df


def add_audit_columns(df: DataFrame, source_file_col: str = None) -> DataFrame:
    """
    Add audit columns (_source_file and _ingested_at) to a DataFrame.

    Args:
        df: Input DataFrame
        source_file_col: Column name containing the source file path (or literal string).
                        If None, uses a placeholder.

    Returns:
        DataFrame with audit columns added
    """
    if source_file_col:
        df = df.withColumn("_source_file", col(source_file_col))
    else:
        df = df.withColumn("_source_file", expr("'unknown'"))

    df = df.withColumn("_ingested_at", current_timestamp())
    return df


def flatten_injury(df: DataFrame) -> DataFrame:
    """
    Transform injury news data: explode items, trim text fields, parse timestamp.

    Expected input columns:
      - items: Array of item objects
      - _metadata_file_path (or source_file if passed separately)
      - _metadata_file_modification_time (or snapshot_at if passed separately)

    Columns source_file and snapshot_at are expected to already be present
    or to be added by the caller.

    Args:
        df: Input DataFrame with items array

    Returns:
        Flattened DataFrame with one row per injury record
    """
    # Explode items array
    df = df.select(
        col("items").alias("item"),
        col("source_file"),
        col("snapshot_at"),
    ).select(
        "item.*",
        "source_file",
        "snapshot_at",
    )

    # Trim text fields
    df = df.select(
        col("id"),
        col("player_id"),
        col("team_id"),
        trim(col("title")).alias("title"),
        trim(col("desc")).alias("description"),
        trim(col("impact")).alias("impact"),
        col("author"),
        col("categories"),
        col("link"),
        col("sport_id"),
        to_timestamp(col("created"), "yyyy-MM-dd HH:mm:ss").alias("created_at"),
        col("snapshot_at"),
        col("source_file"),
        current_timestamp().alias("ingested_at"),
    )

    return df


def dedupe_latest(df: DataFrame, keys: list) -> DataFrame:
    """
    Deduplicate DataFrame keeping the record with the latest snapshot_at per key.

    Uses a row_number window function to identify duplicates within a batch.
    The caller is responsible for ensuring snapshot_at is present.

    Args:
        df: Input DataFrame
        keys: List of column names forming the composite key

    Returns:
        Deduplicated DataFrame
    """
    if not keys:
        raise ValueError("keys cannot be empty")

    window = Window.partitionBy(*keys).orderBy(col("snapshot_at").desc())
    df = df.withColumn("_rn", row_number().over(window))
    df = df.filter(col("_rn") == 1).drop("_rn")

    return df


def build_stats_map(df: DataFrame, known_cols: list) -> DataFrame:
    """
    Extract dynamic stats into a MAP column, filtering out known columns.

    For standings and game_team_stats tables where ESPN stats are stored
    as flat columns with varying names. This function builds a MAP containing
    only the stat columns, excluding known fields like teamId, teamName, etc.

    Args:
        df: Input DataFrame with dynamic stat columns
        known_cols: List of column names to exclude from the stats map
                   (e.g., ['teamId', 'teamName', 'teamAbbr', 'source_file', 'snapshot_at', 'ingested_at'])

    Returns:
        DataFrame with a new 'stats' column (MapType) containing remaining fields
    """
    # Build a map of all columns, then filter to exclude known ones
    stats_map = from_json(
        to_json(struct("*")),
        MapType(StringType(), StringType())
    )

    # Filter out the known columns
    filtered_stats = map_filter(
        stats_map,
        lambda k, v: ~col(k).isin(known_cols)
    )

    df = df.withColumn("stats", filtered_stats)

    # Drop the original stat columns, keep only the known ones and stats
    cols_to_keep = [c for c in df.columns if c in known_cols or c == "stats"]
    df = df.select(*cols_to_keep)

    return df


def upsert_latest(spark, batch_df: DataFrame, table: str, keys: list) -> None:
    """
    Upsert a batch of records into a Delta table.

    Merges using the following logic:
    - Insert new records (not in table)
    - Update existing records when source snapshot_at > target snapshot_at
    - Ignore when source snapshot_at <= target snapshot_at (already seen)

    Args:
        spark: SparkSession
        batch_df: Batch DataFrame to merge
        table: Target Delta table name (e.g., "nfl.injury_news")
        keys: List of column names forming the composite key
    """
    if not keys:
        raise ValueError("keys cannot be empty")

    from delta import DeltaTable

    delta_table = DeltaTable.forName(spark, table)

    # Build the merge key condition: t.col1 = s.col1 AND t.col2 = s.col2 ...
    key_condition = " AND ".join([f"t.{k} = s.{k}" for k in keys])

    # Merge with update-on-newer-snapshot semantics
    (
        delta_table.alias("t")
        .merge(batch_df.alias("s"), key_condition)
        .whenMatchedUpdateAll(condition="s.snapshot_at > t.snapshot_at")
        .whenNotMatchedInsertAll()
        .execute()
    )


# ============================================================================
# Target schemas: Source of truth for cleaned Delta Lake tables
# ============================================================================

target_schemas = {
    "injury_news": injury_file_schema(),
    "games": games_schema(),
    "teams": teams_schema(),
    "players": players_schema(),
    "rosters": rosters_schema(),
    "schedules": schedules_schema(),
    # Add more as schemas are defined
}


# ============================================================================
# Conform functions: Transform ESPN data to cleaned Delta schema
# ============================================================================

def _enforce_output_schema(df: DataFrame, target_schema: StructType) -> DataFrame:
    """
    Ensure DataFrame output matches target schema exactly.
    Selects only columns defined in schema, in schema order.

    Args:
        df: Input DataFrame (may have extra columns)
        target_schema: Target StructType defining output columns

    Returns:
        DataFrame with exactly the columns in target schema
    """
    target_cols = [f.name for f in target_schema.fields]
    return df.select(*target_cols)


def conform_games(df: DataFrame) -> DataFrame:
    """
    Conform games data to cleaned schema.

    Input: ESPN game summary with `date`, `venue`, `city`, `attendance`
    Output: `game_id`, `venue`, `game_ts_utc`, `_source_file`, `_ingested_at`

    - Parses date to timestamp
    - Drops attendance (always empty in ESPN data)

    Args:
        df: Input DataFrame with ESPN game data

    Returns:
        Conformed DataFrame
    """
    # Enforce output schema: select only columns in target_schemas['games']
    return _enforce_output_schema(df, target_schemas['games'])


def conform_schedules(df: DataFrame) -> DataFrame:
    """
    Conform schedules data to cleaned schema.

    Input: ESPN schedule with snake_case names (from pluckScheduleFields)
    Output: typed columns, nulled scores for non-Final games

    - Casts to proper types (long for ids, boolean for completed)
    - Nulls home_score/away_score unless status == 'Final'

    Args:
        df: Input DataFrame with ESPN schedule data (from pluckScheduleFields)

    Returns:
        Conformed DataFrame
    """
    # Null out scores if game is not Final
    df = df.withColumn(
        "home_score",
        expr("CASE WHEN status = 'Final' THEN home_score ELSE NULL END")
    ).withColumn(
        "away_score",
        expr("CASE WHEN status = 'Final' THEN away_score ELSE NULL END")
    )

    # Cast to proper types
    df = df.select(
        col("season").cast("long"),
        col("id").alias("game_id").cast("long"),
        col("week").cast("long"),
        col("home_team_id").cast("long"),
        col("home_team"),
        col("home_score").cast("long"),
        col("away_team_id").cast("long"),
        col("away_team"),
        col("away_score").cast("long"),
        col("venue"),
        col("completed").cast("boolean"),
        col("status"),
        col("_source_file"),
        col("_ingested_at"),
    )

    # Enforce output schema: select only columns in target_schemas['schedules']
    return _enforce_output_schema(df, target_schemas['schedules'])


def conform_rosters(df: DataFrame) -> DataFrame:
    """
    Conform rosters data to cleaned schema.

    Input: ESPN rosters with camelCase names
    Output: snake_case, typed ints

    - Snake-cases column names
    - Casts to int: season, team_id, player_id, jersey, age, height, weight, experience_years

    Args:
        df: Input DataFrame with ESPN rosters data

    Returns:
        Conformed DataFrame
    """
    df = snake_case_columns(df)

    # Cast to int
    int_cols = ["season", "team_id", "player_id", "jersey", "age", "height", "weight", "experience_years"]
    for c in int_cols:
        if c in df.columns:
            df = df.withColumn(c, col(c).cast("long"))

    # Select and order columns matching cleaned schema
    df = df.select(
        col("season"),
        col("team_id"),
        col("player_id"),
        col("player_name"),
        col("position_group"),
        col("position"),
        col("jersey"),
        col("age"),
        col("height"),
        col("weight"),
        col("experience_years"),
        col("college"),
        col("_source_file"),
        col("_ingested_at"),
    )

    return df


def conform_standings(df: DataFrame) -> DataFrame:
    """
    Conform standings data to cleaned schema.

    Input: ESPN standings with camelCase and mixed types
    Output: snake_case, typed ints, boolean fields

    - Snake-cases all columns
    - Casts to int: season, team_id, and all numeric stat columns
    - Handles clincher as boolean

    Args:
        df: Input DataFrame with ESPN standings data

    Returns:
        Conformed DataFrame
    """
    df = snake_case_columns(df)

    # Cast int columns
    int_cols = [
        "season", "team_id", "differential", "games_behind", "losses",
        "playoff_seed", "point_differential", "points_against", "points_for",
        "streak", "ties", "wins", "division_losses", "division_ties", "division_wins",
        "locked_div_rank"
    ]
    for c in int_cols:
        if c in df.columns:
            df = df.withColumn(c, col(c).cast("long"))

    # Cast clincher to boolean
    if "clincher" in df.columns:
        df = df.withColumn("clincher", col("clincher").cast("boolean"))

    # Select columns matching cleaned schema
    df = df.select(
        col("season"),
        col("conference"),
        col("team_id"),
        col("team_name"),
        col("clincher"),
        col("differential"),
        col("games_behind"),
        col("losses"),
        col("playoff_seed"),
        col("point_differential"),
        col("points_against"),
        col("points_for"),
        col("streak"),
        col("ties"),
        col("win_percent").cast("double"),
        col("wins"),
        col("division_losses"),
        col("division_record"),
        col("division_ties"),
        col("division_wins"),
        col("locked_div_rank"),
        col("_source_file"),
        col("_ingested_at"),
    )

    return df


def conform_game_team_stats(df: DataFrame) -> DataFrame:
    """
    Conform game team stats data to cleaned schema.

    Input: ESPN game stats with camelCase, paired strings (e.g., "5-8"), dict stats
    Output: snake_case, split pairs (conversions/attempts), possession seconds, typed ints

    Complex transformations:
    - Splits paired stats: "5-8" → third_down_conversions=5, third_down_attempts=8
    - Converts possession "34:42" → possession_seconds=2082
    - Drops *_per_game* columns (season-to-date averages, belong elsewhere)
    - Drops rows where all stats are null (games not yet played)

    Args:
        df: Input DataFrame with ESPN game-team-stats data

    Returns:
        Conformed DataFrame
    """
    df = snake_case_columns(df)

    # Helper function to split "5-8" style strings
    def split_stat_pair(data, col_name, left, right, sep="-"):
        """Split a string like '5-8' into two int columns."""
        if col_name in data.columns:
            parts = expr(f"split(`{col_name}`, '{sep}')")
            data = data.withColumn(
                left,
                expr(f"try_cast(element_at(split(`{col_name}`, '{sep}'), 1), 'long')")
            ).withColumn(
                right,
                expr(f"try_cast(element_at(split(`{col_name}`, '{sep}'), 2), 'long')")
            ).drop(col_name)
        return data

    # Split paired stats
    df = split_stat_pair(df, "third_down_eff", "third_down_conversions", "third_down_attempts", "-")
    df = split_stat_pair(df, "fourth_down_eff", "fourth_down_conversions", "fourth_down_attempts", "-")
    df = split_stat_pair(df, "completion_attempts", "pass_completions", "pass_attempts", "/")
    df = split_stat_pair(df, "sacks_yards_lost", "sacks", "sack_yards_lost", "-")
    df = split_stat_pair(df, "red_zone_attempts", "red_zone_scores", "red_zone_attempts", "-")
    df = split_stat_pair(df, "total_penalties_yards", "penalties", "penalty_yards", "-")

    # Convert possession time MM:SS to seconds
    if "possession_time" in df.columns:
        df = df.withColumn(
            "possession_seconds",
            (expr("cast(element_at(split(`possession_time`, ':'), 1) as long)") * 60 +
             expr("cast(element_at(split(`possession_time`, ':'), 2) as long)"))
            .cast("long")
        ).drop("possession_time")

    # Drop per-game columns (season-to-date averages)
    per_game_cols = [c for c in df.columns if "per_game" in c]
    if per_game_cols:
        df = df.drop(*per_game_cols)

    # Drop rows where all stats are null (games not yet played)
    stat_cols = [
        c for c in df.columns
        if c not in ["game_id", "team_id", "team_name", "home_away", "_source_file", "_ingested_at"]
    ]
    if stat_cols:
        null_checks = [col(c).isNull() for c in stat_cols]
        df = df.filter(~expr(" AND ".join([f"`{c}` IS NULL" for c in stat_cols])))

    # Cast numeric columns to long
    numeric_cols = [
        "first_downs", "first_downs_passing", "first_downs_rushing", "first_downs_penalty",
        "total_offensive_plays", "total_yards", "total_drives", "net_passing_yards",
        "interceptions", "rushing_yards", "rushing_attempts", "turnovers", "fumbles_lost",
        "defensive_touchdowns", "third_down_conversions", "third_down_attempts",
        "fourth_down_conversions", "fourth_down_attempts", "pass_completions", "pass_attempts",
        "sacks", "sack_yards_lost", "red_zone_scores", "red_zone_attempts", "penalties", "penalty_yards",
        "possession_seconds"
    ]
    for c in numeric_cols:
        if c in df.columns:
            df = df.withColumn(c, col(c).cast("long"))

    # Cast float columns
    float_cols = ["yards_per_play", "yards_per_pass", "yards_per_rush_attempt"]
    for c in float_cols:
        if c in df.columns:
            df = df.withColumn(c, col(c).cast("double"))

    return df

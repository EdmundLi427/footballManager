# Column Transformation Mappings: API → Raw → Cleaned

This document tracks every column transformation from ESPN API payloads through raw CSVs to the cleaned Delta schema. Use this as a reference when writing `conform_*` functions and Azure plucking logic.

**Legend:**
- **API field:** ESPN API JSON field name (camelCase)
- **Raw CSV:** Column in `etl/data/raw/` (snake_case via auto `to_snake()`)
- **Cleaned Dest:** Column in `etl/data/clean/` and target Delta tables
- **Type/Logic:** Spark/Pandas transformation applied in `dataClean.ipynb`
- **Source:** Which Azure trigger provides this data (or "CSV only" if not from ESPN)

---

## teams

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| `id` | `team_id` | `team_id` | string → int | espnClient (teamsPlayersTrigger) |
| `uid` | `uid` | `uid` | string | teamsPlayersTrigger |
| `slug` | `slug` | `slug` | string | teamsPlayersTrigger |
| `abbreviation` | `abbreviation` | `abbreviation` | string | teamsPlayersTrigger |
| `displayName` | `display_name` | `team_name` | string; rename | teamsPlayersTrigger |
| `shortDisplayName` | `short_display_name` | `short_name` | string; rename | teamsPlayersTrigger |
| `location` | `location` | `location` | string | teamsPlayersTrigger |
| `name` | `name` | `name` | string | teamsPlayersTrigger |
| `color` | `color` | `color` | string; prefix `#`, lower-case | teamsPlayersTrigger |
| `alternateColor` | `alternate_color` | `alternate_color` | string; prefix `#`, lower-case | teamsPlayersTrigger |
| `isActive` | `is_active` | `is_active` | string → boolean | teamsPlayersTrigger |

**Audit columns:** `_source_file`, `_ingested_at` (added by Auto Loader)

**Status:** ESPN pluck is incomplete (missing `location`, `name`, `shortDisplayName`). Consider keeping this table CSV-only or extending the pluck.

---

## players

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| `id` | `id` | `player_id` | string → int; rename | teamsPlayersTrigger |
| `uid` | `uid` | `uid` | string | teamsPlayersTrigger |
| `guid` | `guid` | `guid` | string | teamsPlayersTrigger |
| `firstName` | `first_name` | `first_name` | string | teamsPlayersTrigger |
| `lastName` | `last_name` | `last_name` | string | teamsPlayersTrigger |
| `fullName` | `full_name` | `full_name` | string | teamsPlayersTrigger |
| `displayName` | `display_name` | `display_name` | string | teamsPlayersTrigger |
| `shortName` | `short_name` | `short_name` | string | teamsPlayersTrigger |
| `weight` | `weight` | `weight_lbs` | string → float → int; rename; drop `displayWeight` | teamsPlayersTrigger |
| `height` | `height` | `height_in` | string → float → int; rename; drop `displayHeight` | teamsPlayersTrigger |
| `age` | `age` | `age` | string → int | teamsPlayersTrigger |
| `dateOfBirth` | `date_of_birth` | `date_of_birth` | string → date (UTC) | teamsPlayersTrigger |
| `jersey` | `jersey` | `jersey` | string → int | teamsPlayersTrigger |
| `active` | `active` | `active` | string → boolean | teamsPlayersTrigger |
| `birthPlace.city` | `birth_place_city` | `birth_place_city` | string | teamsPlayersTrigger |
| `birthPlace.state` | `birth_place_state` | `birth_place_state` | string | teamsPlayersTrigger |
| `birthPlace.country` | `birth_place_country` | `birth_place_country` | string | teamsPlayersTrigger |
| `experience.years` | `experience_years` | `experience_years` | string → int | teamsPlayersTrigger |
| `hand.type` | `hand_type` | `hand_type` | string; lower-case | teamsPlayersTrigger |
| | (dropped) | | `hand_abbreviation`, `hand_displayValue`, `dateOfDeath`, `middleName`, `citizenship` present in raw but handled separately | |
| `dateOfDeath` | `date_of_death` | `date_of_death` | string → date (UTC) | teamsPlayersTrigger |
| `middleName` | `middle_name` | `middle_name` | string | teamsPlayersTrigger |
| (citizenship) | `citizenship` | `citizenship` | string | teamsPlayersTrigger |

**Audit columns:** `_source_file`, `_ingested_at`

**Status:** ESPN pluck is incomplete (player rosters are season-filtered; static player bios lag). Consider keeping CSV-only.

---

## rosters

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| (none; provided in source) | `season` | `season` | string → int | espnClient (standingsRostersTrigger) |
| `teamId` | `team_id` | `team_id` | string → int | standingsRostersTrigger |
| `playerId` | `player_id` | `player_id` | string → int | standingsRostersTrigger |
| `playerName` | `full_name` | `player_name` | string; rename | standingsRostersTrigger |
| `position_group` | `position_group` | `position_group` | string | standingsRostersTrigger |
| `position` | `position` | `position` | string | standingsRostersTrigger |
| `jersey` | `jersey` | `jersey` | string → int | standingsRostersTrigger |
| `age` | `age` | `age` | string → int | standingsRostersTrigger |
| `height` | `height` | `height_in` | string → int; rename | standingsRostersTrigger |
| `weight` | `weight` | `weight_lbs` | string → int; rename | standingsRostersTrigger |
| `experience` | `experience_years` | `experience_years` | string → int | standingsRostersTrigger |
| `college` | `college` | `college` | string | standingsRostersTrigger |

**Composite key:** `season, team_id, player_id`

**Audit columns:** `_source_file`, `_ingested_at`

**Bug to fix:** ESPN records lack `homeAway` (not in rosters); verify against live payload.

---

## schedules

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| (none; from schedule context) | `season` | `season` | string → int | gameDataTrigger |
| `id` | `game_id` | `game_id` | string → int | gameDataTrigger |
| `date` | `date` | `game_ts_utc` | string → timestamp (UTC); rename; drop raw `date` | gameDataTrigger |
| `week.number` | `week` | `week` | string → int | gameDataTrigger |
| `competitions[0].status.type` | `status` | `status` | string | gameDataTrigger |
| `competitions[0].status.type === 'Final'` | (inferred) | `completed` | string → boolean | gameDataTrigger |
| `competitors[home].team.id` | `home_team_id` | `home_team_id` | string → int | gameDataTrigger |
| (lookup from teams table) | (not in ESPN) | `home_team` | string; denormalized from teams | gameDataTrigger |
| `competitors[home].score.value` | `home_score` | `home_score` | dict → extract `.value` → int; null if not Final | gameDataTrigger |
| `competitors[away].team.id` | `away_team_id` | `away_team_id` | string → int | gameDataTrigger |
| (lookup from teams table) | (not in ESPN) | `away_team` | string; denormalized from teams | gameDataTrigger |
| `competitors[away].score.value` | `away_score` | `away_score` | dict → extract `.value` → int; null if not Final | gameDataTrigger |
| `competitions[0].venue.fullName` | `venue` | `venue` | string | gameDataTrigger |

**Primary key:** `game_id`

**Audit columns:** `_source_file`, `_ingested_at`

**Bugs to fix:**
- `status` is an object (`type`, `description`), not a string; compare `type === 'Final'`
- Scores are dicts `{value, displayValue}`; extract `.value`
- Missing `season` (must derive from game date or pass separately)
- Missing `home_team`, `away_team` (denormalize from teams table or include in pluck)

---

## games

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| `id` | `game_id` | `game_id` | string → int | gameDataTrigger |
| `date` | `date` | `game_ts_utc` | string → timestamp (UTC); drop raw `date` | gameDataTrigger |
| `gameInfo.venue.fullName` | `venue` | `venue` | string | gameDataTrigger |
| `gameInfo.attendance` | `attendance` | (dropped) | empty in source; dropped in cleaned | gameDataTrigger |

**Primary key:** `game_id`

**Audit columns:** `_source_file`, `_ingested_at`

**Status:** Minimal table; `attendance` is always empty, so it's removed.

---

## standings

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| (none; from season context) | `season` | `season` | string → int | standingsRostersTrigger |
| `id` | `team_id` | `team_id` | string → int | standingsRostersTrigger |
| `displayName` | `team_name` | `team_name` | string | standingsRostersTrigger |
| `subgroup.name` (or `group`) | `conference` | `conference` | string; drop redundant `group` if == `conference` | standingsRostersTrigger |
| `stats[name='clincher'].value` | `clincher` | `clincher` | string → boolean (fillna 0) | standingsRostersTrigger |
| `stats[name='differential'].value` | `differential` | `differential` | string → int | standingsRostersTrigger |
| `stats[name='gamesBehind'].value` | `games_behind` | `games_behind` | string → int | standingsRostersTrigger |
| `stats[name='losses'].value` | `losses` | `losses` | string → int | standingsRostersTrigger |
| `stats[name='playoffSeed'].value` | `playoff_seed` | `playoff_seed` | string → int | standingsRostersTrigger |
| `stats[name='pointDifferential'].value` | `point_differential` | `point_differential` | string → int | standingsRostersTrigger |
| `stats[name='pointsAgainst'].value` | `points_against` | `points_against` | string → int | standingsRostersTrigger |
| `stats[name='pointsFor'].value` | `points_for` | `points_for` | string → int | standingsRostersTrigger |
| `stats[name='streak'].value` | `streak` | `streak` | string → int | standingsRostersTrigger |
| `stats[name='ties'].value` | `ties` | `ties` | string → int | standingsRostersTrigger |
| `stats[name='winPercent'].value` | `win_percent` | `win_percent` | string → float | standingsRostersTrigger |
| `stats[name='wins'].value` | `wins` | `wins` | string → int | standingsRostersTrigger |
| `stats[name='divisionLosses'].value` | `division_losses` | `division_losses` | string → int | standingsRostersTrigger |
| `stats[name='divisionRecord'].value` | `division_record` | `division_record` | string → int (parsed from "W-L") | standingsRostersTrigger |
| `stats[name='divisionTies'].value` | `division_ties` | `division_ties` | string → int | standingsRostersTrigger |
| `stats[name='divisionWins'].value` | `division_wins` | `division_wins` | string → int | standingsRostersTrigger |
| `stats[name='lockedDivRank'].value` | `locked_div_rank` | `locked_div_rank` | string → int | standingsRostersTrigger |
| (dropped: `group` if redundant, record columns if empty) | `overall`, `Home`, `Road`, `vs. Div.`, `vs. Conf.` | (dropped) | empty in source | standingsRostersTrigger |

**Composite key:** `season, team_id`

**Audit columns:** `_source_file`, `_ingested_at`

**Note:** ESPN stats are flat key-value pairs; use `map_filter` to extract only known stats and drop the rest.

---

## game_team_stats

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| (from wrapper) | `game_id` | `game_id` | string → int | gameDataTrigger |
| `teamId` | `team_id` | `team_id` | string → int | gameDataTrigger |
| `teamName` | `team_name` | `team_name` | string | gameDataTrigger |
| (missing in ESPN!) | (missing) | `home_away` | string (H/A); **BUG: ESPN doesn't provide this** | gameDataTrigger |
| `stats[name='firstDowns'].value` | `first_downs` | `first_downs` | string → int | gameDataTrigger |
| `stats[name='firstDownsPassing'].value` | `first_downs_passing` | `first_downs_passing` | string → int | gameDataTrigger |
| `stats[name='firstDownsRushing'].value` | `first_downs_rushing` | `first_downs_rushing` | string → int | gameDataTrigger |
| `stats[name='firstDownsPenalty'].value` | `first_downs_penalty` | `first_downs_penalty` | string → int | gameDataTrigger |
| `stats[name='thirdDownEff'].value` | `third_down_eff` | `third_down_conversions`, `third_down_attempts` | string "5-8" → split → 2 ints | gameDataTrigger |
| `stats[name='fourthDownEff'].value` | `fourth_down_eff` | `fourth_down_conversions`, `fourth_down_attempts` | string "1-3" → split → 2 ints | gameDataTrigger |
| `stats[name='totalOffensivePlays'].value` | `total_offensive_plays` | `total_offensive_plays` | string → int | gameDataTrigger |
| `stats[name='totalYards'].value` | `total_yards` | `total_yards` | string → int | gameDataTrigger |
| `stats[name='yardsPerPlay'].value` | `yards_per_play` | `yards_per_play` | string → float | gameDataTrigger |
| `stats[name='totalDrives'].value` | `total_drives` | `total_drives` | string → int | gameDataTrigger |
| `stats[name='netPassingYards'].value` | `net_passing_yards` | `net_passing_yards` | string → int | gameDataTrigger |
| `stats[name='completionAttempts'].value` | `completion_attempts` | `pass_completions`, `pass_attempts` | string "12/20" → split (/) → 2 ints | gameDataTrigger |
| `stats[name='yardsPerPass'].value` | `yards_per_pass` | `yards_per_pass` | string → float | gameDataTrigger |
| `stats[name='interceptions'].value` | `interceptions` | `interceptions` | string → int | gameDataTrigger |
| `stats[name='sacksYardsLost'].value` | `sacks_yards_lost` | `sacks`, `sack_yards_lost` | string "2-14" → split → 2 ints | gameDataTrigger |
| `stats[name='rushingYards'].value` | `rushing_yards` | `rushing_yards` | string → int | gameDataTrigger |
| `stats[name='rushingAttempts'].value` | `rushing_attempts` | `rushing_attempts` | string → int | gameDataTrigger |
| `stats[name='yardsPerRushAttempt'].value` | `yards_per_rush_attempt` | `yards_per_rush_attempt` | string → float | gameDataTrigger |
| `stats[name='redZoneAttempts'].value` | `red_zone_attempts` | `red_zone_scores`, `red_zone_attempts` | string "3-5" → split → 2 ints | gameDataTrigger |
| `stats[name='totalPenaltiesYards'].value` | `total_penalties_yards` | `penalties`, `penalty_yards` | string "5-50" → split → 2 ints | gameDataTrigger |
| `stats[name='turnovers'].value` | `turnovers` | `turnovers` | string → int | gameDataTrigger |
| `stats[name='fumblesLost'].value` | `fumbles_lost` | `fumbles_lost` | string → int | gameDataTrigger |
| `stats[name='defensiveTouchdowns'].value` | `defensive_touchdowns` | `defensive_touchdowns` | string → int | gameDataTrigger |
| `stats[name='possessionTime'].value` | `possession_time` | `possession_seconds` | string "34:42" → MM:SS → (MM*60 + SS) | gameDataTrigger |
| (dropped) | `*_per_game*` | (dropped) | season-to-date averages; belong in team-season table | gameDataTrigger |

**Composite key:** `game_id, team_id`

**Audit columns:** `_source_file`, `_ingested_at`

**Bugs to fix:**
- **CRITICAL:** ESPN data lacks `homeAway` — need to infer from schedules or add it upstream
- ESPN stats are flat key-value pairs with camelCase names; map them to the split/parsed columns
- Rows with all-null stats (games not yet played) are dropped

---

## injury_news

| API field | Raw CSV | Cleaned Dest | Type/Logic | Source |
|---|---|---|---|---|
| `items[].id` | (from array) | `id` | int | newsTrigger |
| `items[].player_id` | (from array) | `player_id` | int | newsTrigger |
| `items[].team_id` | (from array) | `team_id` | string | newsTrigger |
| `items[].title` | (from array) | `title` | string; trim | newsTrigger |
| `items[].desc` | (from array) | `description` | string; rename; trim | newsTrigger |
| `items[].impact` | (from array) | `impact` | string; trim | newsTrigger |
| `items[].author` | (from array) | `author` | string | newsTrigger |
| `items[].categories` | (from array) | `categories` | array<string> | newsTrigger |
| `items[].link` | (from array) | `link` | string | newsTrigger |
| `items[].sport_id` | (from array) | `sport_id` | string | newsTrigger |
| `items[].created` | (from array) | `created_at` | string → timestamp ("yyyy-MM-dd HH:mm:ss") | newsTrigger |
| (from file metadata) | (from Auto Loader) | `snapshot_at` | timestamp | newsTrigger |

**Primary key:** `id`

**Audit columns:** `_source_file`, `_ingested_at`

**Note:** This is a new table with no raw CSV; the FantasyPros JSON response is exploded into individual rows.

---

## Summary: Common Transformations

| Type | Rule | Example |
|---|---|---|
| **Snake case** | Databricks Auto Loader `to_snake()` converts camelCase, spaces, symbols | `displayName` → `display_name`, `birthPlace.city` → `birth_place_city` |
| **Int cast** | string → `pd.to_numeric().astype('Int64')` (nullable) or Spark `cast('long')` | `"28"` → `28` |
| **Boolean** | string lower-case match: `"true"` → `True`, `"false"` → `False` | `"True"` → `true` |
| **Date** | string → `pd.to_datetime(..., utc=True).dt.date` or Spark `to_timestamp()` | `"1998-03-15"` → date |
| **Timestamp** | string with format → `pd.to_datetime(..., utc=True)` or Spark `to_timestamp(col, "yyyy-MM-dd HH:mm:ss")` | `"2026-10-01 10:30:00"` → timestamp |
| **Pair split** | string "5-8" → two int columns | `"third_down_eff": "5-8"` → `third_down_conversions=5, third_down_attempts=8` |
| **Color prefix** | add `#` and lower-case | `"FF6600"` → `"#ff6600"` |
| **Null scores** | set to null unless `status == 'Final'` | game not played: `home_score` → `null` |
| **Dict extract** | ESPN `{value, displayValue}` → extract `.value` | `{"value": 21.0, "displayValue": "21"}` → `21` |
| **Denormalization** | join team names / positions into stats tables | `game_team_stats.home_away` derived from schedule |
| **Drop columns** | remove `display_*`, `per_game_*`, empty columns | `displayWeight`, `displayHeight`, `*_per_game*`, `attendance` |

---

## Implementation Checklist

- [ ] Verify ESPN payload shapes against live API (or existing fixtures)
- [ ] Write `conform_games()`, `conform_schedules()`, `conform_rosters()`, `conform_standings()`, `conform_game_team_stats()` in `databricks/lib/transforms.py`
- [ ] Update `espnAssemble.js` plucks to include missing fields (season, team names, homeAway) or mark as CSV-only
- [ ] Create Layer 2 fixtures with correct transformed shapes
- [ ] Add Spark tests for each conform function (output schema, parsed values, null handling)
- [ ] Update notebook-structure tests for new keys and removed dead code

# Data transforms: ESPN / FantasyPros → `nfl.*` Delta tables

This is the reference for how every silver table is produced. The code is the source of truth. If this doc and the code disagree, fix the doc:

| What | Where |
|---|---|
| Raw read schemas, table columns/types/comments/keys | `lib/schemas.py` |
| Parsing rules (one `parse_<table>` per table) | `lib/transforms.py` |
| Table DDL, dedupe, merge | `lib/delta_io.py` |
| Notebooks (thin: read → parse → upsert) | `notebooks/ingest_*.ipynb` |
| What Azure writes to blob | `azure/src/lib/espnAssemble.js` |

## Pipeline

```
ESPN API ──Azure Functions──▶ alsource/espn/<dataset>/YYYY-MM-DD/<ts>.json   (bronze: ESPN JSON, trimmed, never renamed)
                                   │
                    Auto Loader, explicit raw schema (schemas.*_RAW)
                                   ▼
                       transforms.parse_<table>()                          (pure DataFrame → DataFrame)
                                   ▼
          delta_io.upsert_latest(): dedupe per key → MERGE (newer _snapshot_at wins)
                                   ▼
                     nfl.<table>  (silver, snake_case, typed)
```

**Design rules**
- **Azure lands ESPN JSON as returned**, minus subtrees we never read (logos, links, leaders, broadcasts). It does no renaming or typing. If a parser has a bug, fix it and replay the existing blobs; no data is lost.
- **Explicit read schemas, no schema evolution.** Auto Loader reads only the fields declared in `*_RAW`. New ESPN fields are ignored until we add them on purpose. A missing field shows up as NULL in the parser tests, not as a silently NULL-filled table.
- **Every silver table has the exact schema in `TABLES[name].schema`.** Parser output is checked against it in tests. The notebook DDL is generated from it.
- **Keys are `NOT NULL`.** A row with a null key fails the merge loudly instead of corrupting the table.
- **Idempotent merges.** Within a batch, the newest `_snapshot_at` wins per key (ties go to the later file). An existing row updates only if the incoming snapshot is newer. Replaying blobs is safe.
- **IDs are `STRING`** (ESPN ids are opaque). Counts are `BIGINT`, rates `DOUBLE`, instants `TIMESTAMP` (UTC).
- **Safe casts.** ESPN sometimes sends `'-'` or `''` where a number belongs. `to_long`/`to_double` turn anything non-numeric into NULL instead of failing under ANSI mode.

## Audit columns (all ESPN tables)

| Column | Type | Meaning |
|---|---|---|
| `_source_file` | STRING | Blob path the row came from (`_metadata.file_path`) |
| `_snapshot_at` | TIMESTAMP | Blob modification time; decides which version wins |
| `_ingested_at` | TIMESTAMP | When the row was written to Delta |

`nfl.injury_news` predates this convention and uses `source_file`, `snapshot_at`, `ingested_at`.

## Sources and cadence

| Blob prefix | Azure function (schedule, UTC) | ESPN endpoint | Feeds |
|---|---|---|---|
| `espn/teams/` | `fetchTeamsPlayers` (daily 06:15) | `site/v2/.../teams?limit=100` | `nfl.teams` |
| `espn/players/` | `fetchTeamsPlayers` (daily 06:15) | `core/v3/.../seasons/{y}/athletes` (paginated) | `nfl.players` |
| `espn/standings/` | `fetchStandingsRosters` (daily 08:30) | `apis/v2/.../standings?season={y}` | `nfl.standings` |
| `espn/rosters/` | `fetchStandingsRosters` (daily 08:30) | `site/v2/.../teams/{id}/roster?season={y}` ×32 | `nfl.rosters` |
| `espn/schedules/` | `fetchGameData` (every 6h) | `site/v2/.../teams/{id}/schedule?season={y}&seasontype={2,3}` ×32 ×2, deduped by event id (without `seasontype` ESPN returns only the regular season) | `nfl.schedules` |
| `espn/game_summaries/` | `fetchGameData` (every 6h) | `site/v2/.../summary?event={id}` for games that kicked off and are in progress or finished ≤3 days ago (max 50) | `nfl.games`, `nfl.game_team_stats`, `nfl.player_game_stats` |
| `injury/` | `newsTrigger` (every 6h) | FantasyPros `public/v2/json/nfl/news?category=injury` (stored unchanged) | `nfl.injury_news` |

**Historical backfill:** `azure/scripts/backfill-espn.js` lands whole past seasons (schedules, a summary for every completed game, regular + playoff standings) under the same prefixes in the same format, so the same notebooks ingest it. Seasons 2023–2026 were backfilled on 2026-10-06. Those blobs are permanent, so after a table reset you only need to re-run the notebooks, not the script. The exception is `nfl.player_game_stats`: summaries landed before `boxscore.players` was kept have no player data, so re-run the backfill once after deploying that Azure change to re-land them (the newer snapshot wins the merge). Rosters can't be backfilled (ESPN's site roster endpoint is empty for past seasons).

---

## `nfl.teams`
**Grain:** one row per franchise. **Key:** `team_id`.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `id` | `team_id` | STRING | |
| `uid`, `slug`, `abbreviation`, `location`, `name` | same | STRING | |
| `displayName` | `display_name` | STRING | |
| `shortDisplayName` | `short_display_name` | STRING | |
| `color`, `alternateColor` | `color`, `alternate_color` | STRING | `#` + lower-case (`a40227` → `#a40227`); NULL if empty |
| `isActive` | `is_active` | BOOLEAN | |

## `nfl.players`
**Grain:** one row per athlete in ESPN's season athlete index. This index includes retired and inactive players; use `active`. **Key:** `player_id`.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `id` | `player_id` | STRING | |
| `uid`, `guid` | same | STRING | |
| `firstName`, `lastName`, `fullName`, `displayName`, `shortName` | snake_case | STRING | |
| `jersey` | `jersey` | STRING | kept as printed |
| `height` | `height_in` | BIGINT | inches (ESPN sends `70.0`) |
| `weight` | `weight_lbs` | BIGINT | pounds |
| `age` | `age` | BIGINT | |
| `dateOfBirth` | `date_of_birth` | DATE | first 10 chars (`1969-10-27T08:00Z` → `1969-10-27`) |
| `birthPlace.city/state/country` | `birth_city/state/country` | STRING | NULL when ESPN omits `birthPlace` |
| `experience.years` | `experience_years` | BIGINT | |
| `hand.type` | `hand` | STRING | lower-cased (`RIGHT` → `right`) |
| `active` | `active` | BOOLEAN | |

## `nfl.rosters`
**Grain:** one row per (season, team, player) seen on any roster snapshot. **Key:** `season, team_id, player_id`.
Rows are never deleted when a player leaves the team. For the *current* roster, keep each team's rows at its latest `_snapshot_at`.

Landing record: `{season, team: {id, abbreviation, displayName}, athletes: [{position: <group>, items: [player…]}]}`. The parser explodes groups and then players.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `season.year` / `season.type` | `season` / `season_type` | BIGINT | |
| `team.id` / `team.abbreviation` | `team_id` / `team_abbreviation` | STRING | |
| `items[].id` | `player_id` | STRING | |
| `items[].fullName` | `full_name` | STRING | |
| `athletes[].position` | `roster_group` | STRING | `offense`, `defense`, `specialTeam`, `injuredReserveOrOut`, `suspended`, `practiceSquad` |
| `items[].position.abbreviation` / `.name` | `position` / `position_name` | STRING | `WR` / `Wide Receiver` |
| `items[].jersey` | `jersey` | STRING | |
| `items[].age` | `age` | BIGINT | |
| `items[].height` / `weight` | `height_in` / `weight_lbs` | BIGINT | |
| `items[].experience.years` | `experience_years` | BIGINT | |
| `items[].college.name` | `college` | STRING | |
| `items[].status.name` | `status` | STRING | e.g. `Active` |

## `nfl.standings`
**Grain:** one row per team per season type. **Key:** `season, season_type, team_id`.

Landing record: `{season, children: [conference {abbreviation, standings: {season, seasonType, entries: [{team, stats: [{name, value, displayValue}]}]}}]}`. The parser explodes conferences, then entries. Stats are looked up **by `name`**: numbers come from `value` and record strings from `displayValue`.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `children[].standings.season` / `.seasonType` | `season` / `season_type` | BIGINT | |
| `children[].abbreviation` | `conference` | STRING | `AFC` / `NFC` |
| `entries[].team.id/abbreviation/displayName` | `team_id` / `team_abbreviation` / `team_name` | STRING | |
| stat `wins`, `losses`, `ties` (`value`) | same | BIGINT | |
| stat `winPercent` (`value`) | `win_percent` | DOUBLE | |
| stat `pointsFor`, `pointsAgainst`, `pointDifferential` (`value`) | `points_for`, `points_against`, `point_differential` | BIGINT | |
| stat `gamesBehind` (`value`) | `games_behind` | DOUBLE | `displayValue` is `-` for leaders; `value` is 0 |
| stat `playoffSeed` (`value`) | `playoff_seed` | BIGINT | |
| stat `streak` (`displayValue`) | `streak` | STRING | `W3`, `L1` |
| stat `divisionWins/Losses/Ties` (`value`) | `division_wins/losses/ties` | BIGINT | their `displayValue` is oddly formatted (`1.000`), so use `value` |
| stat `overall`, `Home`, `Road`, `vs. Div.`, `vs. Conf.` (`displayValue`) | `overall_record`, `home_record`, `road_record`, `division_record`, `conference_record` | STRING | `W-L[-T]`; these have no numeric `value` |

Not loaded: `differential` (duplicate of `pointDifferential`), `lockedDivRank`, `divisionRecord` (its `value` is always 0; use `division_record`).

## `nfl.schedules`
**Grain:** one row per game on any team's schedule. **Key:** `game_id`.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `id` | `game_id` | STRING | |
| `season.year` | `season` | BIGINT | |
| `seasonType.type` | `season_type` | BIGINT | 1 pre, 2 regular, 3 post. **Week numbers repeat across types**, so filter on both. |
| `week.number` | `week` | BIGINT | |
| `competitions[0].date` (fallback `date`) | `game_ts_utc` | TIMESTAMP | `2026-09-15T00:15Z`, UTC |
| `competitions[0].status.type.name` | `status` | STRING | `STATUS_SCHEDULED`, `STATUS_IN_PROGRESS`, `STATUS_FINAL`, … |
| `competitions[0].status.type.detail` | `status_detail` | STRING | `Final`, `Final/OT`, `Sun, October 4th at 4:25 PM EDT` |
| `competitions[0].status.type.completed` | `completed` | BOOLEAN | |
| competitor where `homeAway='home'`: `team.id/abbreviation/displayName` | `home_team_id`, `home_team_abbreviation`, `home_team_name` | STRING | chosen by `homeAway`, never by array position |
| home competitor `score.value` | `home_score` | BIGINT | NULL while `status.type.state = 'pre'`; live during the game |
| away competitor (same fields) | `away_*` | | |
| `competitions[0].venue.fullName/address.city/address.state` | `venue`, `venue_city`, `venue_state` | STRING | |
| `competitions[0].neutralSite` | `neutral_site` | BOOLEAN | |

Note: the team schedule endpoint returns only the current season type (e.g. regular season). Preseason and postseason games appear once ESPN switches the default.

## `nfl.games`
**Grain:** one row per game that has kicked off (built from game summaries). **Key:** `game_id`.
It shares the first eight columns with `nfl.schedules` (`game_id` … `completed`), parsed from `header.season`, `header.week` and `header.competitions[0]`.

| ESPN field | Column | Type | Rule |
|---|---|---|---|
| `header.competitions[0].competitors[homeAway=home].team.id` / `.score` | `home_team_id` / `home_score` | STRING / BIGINT | header scores are strings (`"31"`) |
| same for away | `away_team_id` / `away_score` | | |
| `gameInfo.venue.fullName/address.city/address.state` | `venue`, `venue_city`, `venue_state` | STRING | |
| `header.competitions[0].neutralSite` | `neutral_site` | BOOLEAN | |
| `gameInfo.attendance` | `attendance` | BIGINT | |

## `nfl.game_team_stats`
**Grain:** one row per team per game box score. **Key:** `game_id, team_id`.

Source: `boxscore.teams[]` of each game summary. Each team has `homeAway`, `team`, and `statistics: [{name, value, displayValue}]`.

**ESPN quirks this handles:**
- `boxscore.teams[0]` is the **away** team. `home_away` comes from the team's own `homeAway` field.
- For paired stats, `value` is a ratio (`thirdDownEff.value = 0.53`) or `'-'`. The real data is in `displayValue` (`"9-17"`), so counts are parsed from `displayValue`.
- `interceptions` appears twice in the list with identical label ("Interceptions thrown") and value. The first is used.
- Rates use `value` (full precision) because `displayValue` is rounded to one decimal.

| ESPN stat (`name`) | Column(s) | Type | Rule |
|---|---|---|---|
| (record) `team.id` / `team.abbreviation` / `homeAway` | `team_id` / `team_abbreviation` / `home_away` | STRING | `home` or `away` |
| `firstDowns`, `firstDownsPassing`, `firstDownsRushing`, `firstDownsPenalty` | `first_downs`, `first_downs_passing`, `first_downs_rushing`, `first_downs_penalty` | BIGINT | `displayValue` |
| `thirdDownEff` | `third_down_conversions`, `third_down_attempts` | BIGINT | `"9-17"` → 9, 17 |
| `fourthDownEff` | `fourth_down_conversions`, `fourth_down_attempts` | BIGINT | `"2-3"` → 2, 3 |
| `totalOffensivePlays`, `totalYards`, `totalDrives`, `netPassingYards` | `total_offensive_plays`, `total_yards`, `total_drives`, `net_passing_yards` | BIGINT | `displayValue` (`totalYards.value` is `'-'`) |
| `yardsPerPlay`, `yardsPerPass`, `yardsPerRushAttempt` | `yards_per_play`, `yards_per_pass`, `yards_per_rush_attempt` | DOUBLE | `value` |
| `completionAttempts` | `pass_completions`, `pass_attempts` | BIGINT | `"15/27"` → 15, 27 |
| `interceptions` (first) | `interceptions_thrown` | BIGINT | |
| `sacksYardsLost` | `sacks`, `sack_yards_lost` | BIGINT | `"2-12"` → 2, 12 (times sacked, yards lost) |
| `rushingYards`, `rushingAttempts` | `rushing_yards`, `rushing_attempts` | BIGINT | |
| `redZoneAttempts` | `red_zone_scores`, `red_zone_attempts` | BIGINT | `"3-5"` → 3, 5 |
| `totalPenaltiesYards` | `penalties`, `penalty_yards` | BIGINT | `"7-60"` → 7, 60 |
| `turnovers`, `fumblesLost`, `defensiveTouchdowns` | `turnovers`, `fumbles_lost`, `defensive_touchdowns` | BIGINT | |
| `possessionTime` | `possession_seconds` | BIGINT | `"33:42"` → 2022 |

## `nfl.player_game_stats`
**Grain:** one row per player per game box score. **Key:** `game_id, player_id`.

Source: `boxscore.players[]` of each game summary (same blobs as `nfl.games`). Azure keeps per team `{team, statistics: [{name, keys, athletes: [{athlete: {id, displayName, jersey}, stats}]}]}`. `keys` and each athlete's `stats` are parallel arrays of strings. Groups: `passing`, `rushing`, `receiving`, `fumbles`, `defensive`, `interceptions`, `kickReturns`, `puntReturns`, `kicking`, `punting`.

**How the parser pivots:** it flattens a team's groups into `(group, keys, athlete, stats)` entries, explodes the distinct athlete ids, and reads each column from that athlete's entry in one group. It doesn't use `groupBy`, because the parser runs on the Auto Loader stream. A player who appears in several groups (a QB in passing, rushing and fumbles) gets one row. A stat the player didn't record is NULL, not 0.

**Not included:** position (join `nfl.rosters` on `player_id` + `season`), fantasy points (compute in SQL so scoring settings can vary), and 2-point conversions (ESPN's box score doesn't have them).

**ESPN quirks this handles:**
- Keys repeat across groups. `interceptions` means thrown in `passing` and caught in `interceptions`, so every lookup is by `(group, key)`.
- Missing values are `'--'` (e.g. `adjQBR`). `to_long`/`to_double` turn them into NULL, and a `stats` array shorter than `keys` gives NULL too.
- Sacks and tackles for loss can be halves (`0.5`), so they're DOUBLE.

| ESPN group: key | Column(s) | Type | Rule |
|---|---|---|---|
| (record) `athlete.id` / `displayName` / `jersey` | `player_id` / `player_name` / `jersey` | STRING | |
| (record) `team.id` / `team.abbreviation` | `team_id` / `team_abbreviation` | STRING | |
| `header.season.year` / `.type` / `header.week` | `season` / `season_type` / `week` | BIGINT | |
| passing: `completions/passingAttempts` | `pass_completions`, `pass_attempts` | BIGINT | `"17/28"` → 17, 28 |
| passing: `passingYards`, `passingTouchdowns`, `interceptions` | `passing_yards`, `passing_tds`, `interceptions_thrown` | BIGINT | |
| passing: `sacks-sackYardsLost` | `sacks_taken`, `sack_yards_lost` | BIGINT | `"4-16"` → 4, 16 |
| passing: `QBRating`, `adjQBR` | `passer_rating`, `qbr` | DOUBLE | |
| rushing: `rushingAttempts`, `rushingYards`, `rushingTouchdowns`, `longRushing` | `rushing_attempts`, `rushing_yards`, `rushing_tds`, `rushing_long` | BIGINT | |
| receiving: `receptions`, `receivingTargets`, `receivingYards`, `receivingTouchdowns`, `longReception` | `receptions`, `targets`, `receiving_yards`, `receiving_tds`, `receiving_long` | BIGINT | |
| fumbles: `fumbles`, `fumblesLost`, `fumblesRecovered` | `fumbles`, `fumbles_lost`, `fumbles_recovered` | BIGINT | |
| defensive: `totalTackles`, `soloTackles`, `passesDefended`, `QBHits`, `defensiveTouchdowns` | `tackles_total`, `tackles_solo`, `passes_defended`, `qb_hits`, `def_tds` | BIGINT | |
| defensive: `sacks`, `tacklesForLoss` | `def_sacks`, `tackles_for_loss` | DOUBLE | |
| interceptions: `interceptions`, `interceptionYards`, `interceptionTouchdowns` | `def_interceptions`, `def_interception_yards`, `def_interception_tds` | BIGINT | |
| kickReturns: `kickReturns`, `kickReturnYards`, `kickReturnTouchdowns` | `kick_returns`, `kick_return_yards`, `kick_return_tds` | BIGINT | |
| puntReturns: `puntReturns`, `puntReturnYards`, `puntReturnTouchdowns` | `punt_returns`, `punt_return_yards`, `punt_return_tds` | BIGINT | |
| kicking: `fieldGoalsMade/fieldGoalAttempts`, `extraPointsMade/extraPointAttempts` | `field_goals_made`, `field_goal_attempts`, `extra_points_made`, `extra_point_attempts` | BIGINT | `"1/1"` → 1, 1 |
| kicking: `longFieldGoalMade`, `totalKickingPoints` | `field_goal_long`, `kicking_points` | BIGINT | |
| punting: `punts`, `puntYards`, `puntsInside20` | `punts`, `punt_yards`, `punts_inside_20` | BIGINT | |

## `nfl.injury_news` (FantasyPros)
**Grain:** one row per article. **Key:** `id`. Source: `injury/` snapshots from `newsTrigger`.

| FantasyPros field | Column | Type | Rule |
|---|---|---|---|
| `items[].id`, `player_id` | `id`, `player_id` | BIGINT | |
| `items[].team_id`, `author`, `link`, `sport_id` | same | STRING | |
| `items[].title`, `impact` | same | STRING | trimmed |
| `items[].desc` | `description` | STRING | trimmed |
| `items[].categories` | `categories` | ARRAY<STRING> | |
| `items[].created` | `created_at` | TIMESTAMP | `yyyy-MM-dd HH:mm:ss` |

---

## Changing a transform

1. Change `lib/schemas.py` (columns, raw fields) and `lib/transforms.py` (rule), then update the table in this doc.
2. Add or adjust a golden-value assertion in `tests/test_transforms.py`.
3. Run `uv run pytest databricks/tests` (Spark tests need Java 17).
4. Deploy. For **additive** columns, `ALTER TABLE nfl.<t> ADD COLUMNS (...)` before running the notebook. For **renames, type changes or rule fixes**, drop the table and its checkpoint (see `notebooks/reset_espn_tables.ipynb`) and re-run the notebook. Auto Loader replays every raw blob.

## Detecting ESPN schema drift

`node azure/scripts/refresh-espn-fixtures.js` re-records small real responses into `azure/test/fixture/espn/` and the landing payloads into `tests/fixtures/espn/`. Then:
- `git diff` on the raw fixtures shows what ESPN changed.
- `cd azure && npm test` checks the landing builders still produce the Databricks fixtures.
- `uv run pytest databricks/tests -m spark` checks the parsers still produce non-null keys and the right schema. Golden-value tests are pinned to game 401872931 (DEN @ KC, 2026 week 1); keep `FIXTURE_GAME_ID` pointing at it or update the expected values.

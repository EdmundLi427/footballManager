"""
ESPN NFL Data Pull — last 5 seasons
====================================

Pulls team, standings, roster, schedule/results, and game-level (box score)
data from ESPN's public (unofficial) API and writes each to its own CSV.

Assumption: "last 5 years" = seasons 2022-2026 (today is Sept 2026, so 2026
is the current/in-progress season). Edit SEASONS below if you meant a
different window, e.g. [2021, 2022, 2023, 2024, 2025].

Requires: requests, pandas   ->  pip install requests pandas

Written as notebook-friendly cells (each "# %%" is a separate cell in
Jupyter / VS Code's interactive window). Run top to bottom.

Notes on reliability, since this is an undocumented API:
- The unscoped `.../athletes` endpoint is known to ignore limit/page
  entirely (confirmed by testing) — this script uses the season-scoped
  equivalent instead, which does paginate correctly.
- Standings and box-score JSON shapes aren't officially documented and can
  vary; the parsing below is written defensively (recursive / .get() based)
  but if ESPN changes their response shape, inspect a single raw response
  with e.g. `import json; print(json.dumps(data, indent=2)[:3000])` before
  assuming the CSV is complete.
- No published rate limit, but a SLEEP delay is included between calls to
  avoid getting throttled/blocked. Increase it if you see failures.
- Full run makes roughly 1,700+ requests (teams x seasons for rosters and
  schedules, plus one call per unique game for box scores). At SLEEP=0.3s
  expect it to take somewhere around 10-20 minutes.
"""

# %%
import time
import json
import requests
import pandas as pd

SEASONS = [2022, 2023, 2024, 2025, 2026]  # last 5 seasons — adjust if needed
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE_BASE = "https://sports.core.api.espn.com/v3/sports/football/nfl"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
SLEEP = 0.3  # polite delay between requests

session = requests.Session()


def get_json(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"FAILED: {url} params={params} -> {e}")
                return None
            time.sleep(1 + attempt)
    return None


# %%
# ---------------------------------------------------------------------------
# 1. TEAMS  ->  teams.csv
# ---------------------------------------------------------------------------
def fetch_teams():
    data = get_json(f"{SITE_BASE}/teams", params={"limit": 100})
    rows = []
    if not data:
        return pd.DataFrame(rows)
    leagues = data.get("sports", [{}])[0].get("leagues", [{}])
    teams = leagues[0].get("teams", []) if leagues else []
    for entry in teams:
        t = entry.get("team", {})
        rows.append({
            "team_id": t.get("id"),
            "uid": t.get("uid"),
            "slug": t.get("slug"),
            "abbreviation": t.get("abbreviation"),
            "displayName": t.get("displayName"),
            "shortDisplayName": t.get("shortDisplayName"),
            "location": t.get("location"),
            "name": t.get("name"),
            "color": t.get("color"),
            "alternateColor": t.get("alternateColor"),
            "isActive": t.get("isActive"),
        })
    return pd.DataFrame(rows)


teams_df = fetch_teams()
teams_df.to_csv("teams.csv", index=False)
print(f"teams.csv: {len(teams_df)} rows")

TEAM_IDS = teams_df["team_id"].dropna().tolist()

# %%
# ---------------------------------------------------------------------------
# 2. STANDINGS (per season)  ->  standings.csv
# ---------------------------------------------------------------------------
def _flatten_standings_group(group, season, conf=None, rows=None):
    if rows is None:
        rows = []
    name = group.get("name")
    entries = (group.get("standings") or {}).get("entries", [])
    for e in entries:
        team = e.get("team", {})
        stats = {s.get("name"): s.get("value") for s in e.get("stats", [])}
        rows.append({
            "season": season,
            "group": name,
            "conference": conf or name,
            "team_id": team.get("id"),
            "team_name": team.get("displayName"),
            **stats,
        })
    for child in group.get("children", []):
        _flatten_standings_group(child, season, conf=conf or name, rows=rows)
    return rows


def fetch_standings(season):
    data = get_json(STANDINGS_URL, params={"season": season})
    rows = []
    if not data:
        return rows
    for group in data.get("children", []):
        rows.extend(_flatten_standings_group(group, season))
    return rows


standings_rows = []
for yr in SEASONS:
    standings_rows.extend(fetch_standings(yr))
    time.sleep(SLEEP)
    print(f"season {yr} standings done")

standings_df = pd.DataFrame(standings_rows)
standings_df.to_csv("standings.csv", index=False)
print(f"standings.csv: {len(standings_df)} rows")

# %%
# ---------------------------------------------------------------------------
# 3. ROSTERS (per team, per season)  ->  rosters.csv
# ---------------------------------------------------------------------------
def fetch_roster(team_id, season):
    data = get_json(f"{SITE_BASE}/teams/{team_id}/roster", params={"season": season})
    rows = []
    if not data:
        return rows
    for group in data.get("athletes", []):
        position_group = group.get("position")
        for p in group.get("items", []):
            rows.append({
                "season": season,
                "team_id": team_id,
                "player_id": p.get("id"),
                "fullName": p.get("fullName"),
                "position_group": position_group,
                "position": (p.get("position") or {}).get("abbreviation"),
                "jersey": p.get("jersey"),
                "age": p.get("age"),
                "height": p.get("height"),
                "weight": p.get("weight"),
                "experience_years": (p.get("experience") or {}).get("years"),
                "college": (p.get("college") or {}).get("name"),
            })
    return rows


roster_rows = []
for yr in SEASONS:
    for tid in TEAM_IDS:
        roster_rows.extend(fetch_roster(tid, yr))
        time.sleep(SLEEP)
    print(f"season {yr} rosters done ({len(roster_rows)} rows so far)")

rosters_df = pd.DataFrame(roster_rows)
rosters_df.to_csv("rosters.csv", index=False)
print(f"rosters.csv: {len(rosters_df)} rows")

# %%
# ---------------------------------------------------------------------------
# 4. PLAYERS master list — union of season-scoped athlete index, deduped
#    (this is the ID/bio index; rosters.csv above has the team-by-team
#    detail with position/jersey/etc.)  ->  players.csv
# ---------------------------------------------------------------------------
def fetch_season_athletes(season):
    items = []
    page = 1
    while True:
        data = get_json(
            f"{CORE_BASE}/seasons/{season}/athletes",
            params={"limit": 1000, "page": page},
        )
        if not data or "items" not in data:
            break
        items.extend(data["items"])
        if page >= data.get("pageCount", page):
            break
        page += 1
        time.sleep(SLEEP)
    return items


all_players = {}
for yr in SEASONS:
    for a in fetch_season_athletes(yr):
        pid = a.get("id")
        if pid and pid not in all_players:
            all_players[pid] = a
    print(f"season {yr}: {len(all_players)} unique players so far")

players_df = pd.json_normalize(list(all_players.values()))
players_df.to_csv("players.csv", index=False)
print(f"players.csv: {len(players_df)} rows")

# %%
# ---------------------------------------------------------------------------
# 5. SCHEDULES / RESULTS (per team, per season)  ->  schedules.csv
# ---------------------------------------------------------------------------
def fetch_schedule(team_id, season):
    data = get_json(f"{SITE_BASE}/teams/{team_id}/schedule", params={"season": season})
    rows = []
    if not data:
        return rows
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        status = comp.get("status", {}).get("type", {})
        rows.append({
            "season": season,
            "game_id": event.get("id"),
            "date": event.get("date"),
            "week": (event.get("week") or {}).get("number"),
            "home_team_id": (home.get("team") or {}).get("id"),
            "home_team": (home.get("team") or {}).get("displayName"),
            "home_score": home.get("score"),
            "away_team_id": (away.get("team") or {}).get("id"),
            "away_team": (away.get("team") or {}).get("displayName"),
            "away_score": away.get("score"),
            "venue": (comp.get("venue") or {}).get("fullName"),
            "completed": status.get("completed"),
            "status": status.get("description"),
        })
    return rows


schedule_rows = []
for yr in SEASONS:
    for tid in TEAM_IDS:
        schedule_rows.extend(fetch_schedule(tid, yr))
        time.sleep(SLEEP)
    print(f"season {yr} schedules done")

schedules_df = pd.DataFrame(schedule_rows)
# each game shows up once per team (home + away) — drop the duplicate
schedules_df = schedules_df.drop_duplicates(subset=["game_id"]).reset_index(drop=True)
schedules_df.to_csv("schedules.csv", index=False)
print(f"schedules.csv: {len(schedules_df)} rows")

# %%
# ---------------------------------------------------------------------------
# 6. GAME DATA — per-game box score detail  ->  games.csv, game_team_stats.csv
# ---------------------------------------------------------------------------
def fetch_game_summary(game_id):
    data = get_json(f"{SITE_BASE}/summary", params={"event": game_id})
    if not data:
        return None, None

    box = data.get("boxscore", {})
    stats_rows = []
    for team in box.get("teams", []):
        t = team.get("team", {})
        stats = {s.get("name"): s.get("displayValue") for s in team.get("statistics", [])}
        stats_rows.append({
            "game_id": game_id,
            "team_id": t.get("id"),
            "team_name": t.get("displayName"),
            "homeAway": team.get("homeAway"),
            **stats,
        })

    competitions = data.get("header", {}).get("competitions", [{}])
    competition = competitions[0] if competitions else {}
    meta = {
        "game_id": game_id,
        "date": competition.get("date"),
        "attendance": competition.get("attendance"),
        "venue": (data.get("gameInfo", {}).get("venue") or {}).get("fullName"),
    }
    return stats_rows, meta


unique_game_ids = schedules_df["game_id"].dropna().unique().tolist()
print(f"Fetching box scores for {len(unique_game_ids)} games — this will take a while")

game_team_stats_rows = []
game_meta_rows = []
for i, gid in enumerate(unique_game_ids):
    stats_rows, meta = fetch_game_summary(gid)
    if stats_rows:
        game_team_stats_rows.extend(stats_rows)
    if meta:
        game_meta_rows.append(meta)
    time.sleep(SLEEP)
    if i % 50 == 0:
        print(f"{i}/{len(unique_game_ids)} games done")

games_df = pd.DataFrame(game_meta_rows)
games_df.to_csv("games.csv", index=False)
print(f"games.csv: {len(games_df)} rows")

game_team_stats_df = pd.DataFrame(game_team_stats_rows)
game_team_stats_df.to_csv("game_team_stats.csv", index=False)
print(f"game_team_stats.csv: {len(game_team_stats_df)} rows")

# %%
print("Done. Files written:")
for f in ["teams.csv", "standings.csv", "rosters.csv", "players.csv", "schedules.csv", "games.csv", "game_team_stats.csv"]:
    print(f" - {f}")
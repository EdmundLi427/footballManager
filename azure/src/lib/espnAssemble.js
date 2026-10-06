/**
 * Builders that turn ESPN API responses into the raw "landing" records written to blob storage.
 *
 * These deliberately keep ESPN's own field names, nesting and types: they only drop
 * subtrees we never read (logos, links, leaders, ...). All renaming, typing and parsing
 * happens downstream in databricks/lib/transforms.py, so a transform bug can be fixed and
 * replayed over existing blobs. See databricks/TRANSFORMS.md for the full contract.
 */

const BLOB_PREFIXES = {
  teams: 'espn/teams',
  players: 'espn/players',
  rosters: 'espn/rosters',
  standings: 'espn/standings',
  schedules: 'espn/schedules',
  gameSummaries: 'espn/game_summaries',
};

function omit(obj, keys) {
  if (!obj) return obj;
  const out = { ...obj };
  for (const key of keys) delete out[key];
  return out;
}

function pick(obj, keys) {
  if (!obj) return obj;
  const out = {};
  for (const key of keys) {
    if (obj[key] !== undefined) out[key] = obj[key];
  }
  return out;
}

const TEAM_REF_KEYS = ['id', 'abbreviation', 'displayName'];

/**
 * Deduplicates schedule events by id (each game appears in both teams' schedules).
 * @param {any[][]} schedulesByTeam - Array of per-team event arrays
 * @returns {any[]} Unique events, first occurrence wins
 */
function dedupeSchedulesByGameId(schedulesByTeam) {
  const seen = new Set();
  const deduplicated = [];

  for (const schedule of schedulesByTeam) {
    for (const entry of schedule) {
      if (!seen.has(entry.id)) {
        seen.add(entry.id);
        deduplicated.push(entry);
      }
    }
  }

  return deduplicated;
}

/** `/teams` response -> team objects. */
function buildTeamsLanding(teamsResponse) {
  const entries = teamsResponse?.sports?.[0]?.leagues?.[0]?.teams || [];
  return entries.map((entry) => omit(entry.team || {}, ['logos', 'links']));
}

/** Core-API athlete items -> athlete objects. */
function buildPlayersLanding(athleteItems) {
  return (athleteItems || []).map((athlete) => omit(athlete, ['links']));
}

/** `/teams/{id}/roster` response -> one record per team, position groups kept nested. */
function buildRosterLanding(rosterResponse) {
  return {
    season: rosterResponse.season,
    team: pick(rosterResponse.team, TEAM_REF_KEYS),
    athletes: (rosterResponse.athletes || []).map((group) => ({
      position: group.position,
      items: (group.items || []).map((player) =>
        omit(player, ['links', 'headshot', 'injuries', 'teams', 'contracts', 'alternateIds'])
      ),
    })),
  };
}

/** `/standings` response -> single record with conference children and their entries. */
function buildStandingsLanding(standingsResponse) {
  return {
    season: standingsResponse.season,
    children: (standingsResponse.children || []).map((child) => ({
      id: child.id,
      name: child.name,
      abbreviation: child.abbreviation,
      standings: {
        season: child.standings?.season,
        seasonType: child.standings?.seasonType,
        entries: (child.standings?.entries || []).map((entry) => ({
          team: omit(entry.team, ['logos', 'links']),
          stats: entry.stats,
        })),
      },
    })),
  };
}

/** Schedule event -> event without links, broadcasts, leaders and logos. */
function trimScheduleEvent(event) {
  return {
    ...omit(event, ['links']),
    competitions: (event.competitions || []).map((competition) => ({
      ...omit(competition, ['broadcasts', 'notes', 'tickets', 'ticketsAvailable']),
      competitors: (competition.competitors || []).map((competitor) => ({
        ...omit(competitor, ['leaders', 'record', 'curatedRank']),
        team: omit(competitor.team, ['logos', 'links']),
      })),
    })),
  };
}

/**
 * `/summary?event={id}` response -> the header, venue, team and player box score subtrees.
 * One record feeds nfl.games, nfl.game_team_stats and nfl.player_game_stats.
 */
function buildGameSummaryLanding(summaryData, gameId) {
  const header = summaryData.header || {};
  const venue = summaryData.gameInfo?.venue;

  return {
    game_id: String(gameId),
    header: {
      id: header.id,
      season: header.season,
      week: header.week,
      competitions: (header.competitions || []).map((competition) => ({
        id: competition.id,
        date: competition.date,
        neutralSite: competition.neutralSite,
        status: competition.status,
        competitors: (competition.competitors || []).map((competitor) => ({
          id: competitor.id,
          homeAway: competitor.homeAway,
          winner: competitor.winner,
          score: competitor.score,
          team: pick(competitor.team, TEAM_REF_KEYS),
        })),
      })),
    },
    gameInfo: {
      venue: venue ? omit(venue, ['images']) : venue,
      attendance: summaryData.gameInfo?.attendance,
    },
    boxscore: {
      teams: (summaryData.boxscore?.teams || []).map((teamData) => ({
        homeAway: teamData.homeAway,
        team: pick(teamData.team, TEAM_REF_KEYS),
        statistics: teamData.statistics || [],
      })),
      players: (summaryData.boxscore?.players || []).map((teamData) => ({
        team: pick(teamData.team, TEAM_REF_KEYS),
        statistics: (teamData.statistics || []).map((group) => ({
          name: group.name,
          keys: group.keys,
          athletes: (group.athletes || []).map((entry) => ({
            athlete: pick(entry.athlete, ['id', 'displayName', 'jersey']),
            stats: entry.stats,
          })),
        })),
      })),
    },
  };
}

/**
 * Picks the game ids that need a summary fetch this run: games that have kicked off and are
 * either still in progress or completed inside the trailing window (catches stat corrections).
 * Future games are skipped — their summaries have no box score yet. Capped.
 *
 * @param {any[]} scheduleEvents - Raw (deduplicated) schedule events
 * @param {Date} now
 * @returns {string[]}
 */
function selectTargetGames(scheduleEvents, now = new Date()) {
  const trailingDays = parseInt(process.env.GAME_SUMMARY_TRAILING_DAYS || '3', 10);
  const maxTargets = parseInt(process.env.GAME_SUMMARY_MAX_TARGETS || '50', 10);
  const cutoff = new Date(now.getTime() - trailingDays * 24 * 60 * 60 * 1000);

  const targets = [];

  for (const event of scheduleEvents) {
    const competition = event.competitions?.[0];
    const completed = competition?.status?.type?.completed === true;
    const gameDate = new Date(competition?.date || event.date);

    const started = gameDate <= now;
    if (started && (!completed || gameDate >= cutoff)) {
      targets.push(event.id);
    }

    if (targets.length >= maxTargets) {
      break;
    }
  }

  return targets;
}

/**
 * Backfill selection: every game that has kicked off and is marked completed. No trailing window
 * or cap — used to land whole past seasons.
 *
 * @param {any[]} scheduleEvents - Raw (deduplicated) schedule events
 * @param {Date} now
 * @returns {string[]}
 */
function selectCompletedGames(scheduleEvents, now = new Date()) {
  return scheduleEvents
    .filter((event) => {
      const competition = event.competitions?.[0];
      const gameDate = new Date(competition?.date || event.date);
      return competition?.status?.type?.completed === true && gameDate <= now;
    })
    .map((event) => event.id);
}

module.exports = {
  BLOB_PREFIXES,
  dedupeSchedulesByGameId,
  buildTeamsLanding,
  buildPlayersLanding,
  buildRosterLanding,
  buildStandingsLanding,
  trimScheduleEvent,
  buildGameSummaryLanding,
  selectTargetGames,
  selectCompletedGames,
};

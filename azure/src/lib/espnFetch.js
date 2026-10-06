/**
 * ESPN fetch + landing assembly shared by the timer triggers and scripts/backfill-espn.js.
 * Item-level failures are collected, never thrown, so callers can report partial results.
 */
const { ESPN_SITES, fetchWithRetry, mapWithConcurrency } = require('./espnClient');
const {
  dedupeSchedulesByGameId,
  trimScheduleEvent,
  buildGameSummaryLanding,
  buildStandingsLanding,
} = require('./espnAssemble');

const REGULAR_AND_POST = [2, 3];

function defaultConcurrency() {
  return parseInt(process.env.ESPN_FETCH_CONCURRENCY || '5', 10);
}

/** `/teams` -> ESPN team ids. */
async function fetchTeamIds(context) {
  context.log('[API_CALL] GET teams (for team IDs)');
  const resp = await fetchWithRetry(`${ESPN_SITES.SITE_BASE}/teams?limit=100`, {}, context);
  const data = await resp.json();
  return (data.sports?.[0]?.leagues?.[0]?.teams || []).map((t) => t.team?.id).filter(Boolean);
}

/**
 * Every team's schedule for one season, per season type. Without `seasontype` ESPN returns only
 * the regular season, so playoffs (type 3) must be requested explicitly.
 * @returns {Promise<{events: any[], failures: {teamId: string, seasonType: number, error: string}[]}>}
 */
async function fetchSeasonSchedules(teamIds, seasonYear, context, options = {}) {
  const seasonTypes = options.seasonTypes || REGULAR_AND_POST;
  const concurrency = options.concurrency || defaultConcurrency();
  const requests = teamIds.flatMap((teamId) => seasonTypes.map((seasonType) => ({ teamId, seasonType })));

  const results = await mapWithConcurrency(requests, concurrency, async ({ teamId, seasonType }) => {
    try {
      const url = `${ESPN_SITES.SITE_BASE}/teams/${teamId}/schedule?season=${seasonYear}&seasontype=${seasonType}`;
      const resp = await fetchWithRetry(url, {}, context);
      const data = await resp.json();
      return { teamId, seasonType, events: (data.events || []).map(trimScheduleEvent), error: null };
    } catch (error) {
      return { teamId, seasonType, events: [], error: error.message };
    }
  });

  const failures = results
    .filter((r) => r.error)
    .map(({ teamId, seasonType, error }) => ({ teamId, seasonType, error }));
  const events = dedupeSchedulesByGameId(results.filter((r) => !r.error).map((r) => r.events));
  return { events, failures };
}

/** `/summary?event={id}` for each game -> game-summary landings. */
async function fetchGameSummaries(gameIds, context, options = {}) {
  const concurrency = options.concurrency || defaultConcurrency();

  const results = await mapWithConcurrency(gameIds, concurrency, async (gameId) => {
    try {
      const resp = await fetchWithRetry(`${ESPN_SITES.SITE_BASE}/summary?event=${gameId}`, {}, context);
      const data = await resp.json();
      return { gameId, landing: buildGameSummaryLanding(data, gameId), error: null };
    } catch (error) {
      return { gameId, landing: null, error: error.message };
    }
  });

  return {
    landings: results.filter((r) => !r.error).map((r) => r.landing),
    failures: results.filter((r) => r.error).map(({ gameId, error }) => ({ gameId, error })),
  };
}

/** `/standings?season=Y&seasontype=T` -> standings landing (one record). */
async function fetchStandings(seasonYear, seasonType, context) {
  const url = `${ESPN_SITES.STANDINGS_URL}?season=${seasonYear}&seasontype=${seasonType}`;
  const resp = await fetchWithRetry(url, {}, context);
  return buildStandingsLanding(await resp.json());
}

module.exports = {
  fetchTeamIds,
  fetchSeasonSchedules,
  fetchGameSummaries,
  fetchStandings,
};

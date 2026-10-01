/**
 * Deduplicates schedule entries by game_id.
 * Each game appears in both teams' schedules; this extracts unique games.
 * @param {any[]} schedulesByTeam - Array of team schedule arrays
 * @returns {any[]} Deduplicated schedule entries
 */
function dedupeSchedulesByGameId(schedulesByTeam) {
  const seen = new Set();
  const deduplicated = [];

  for (const schedule of schedulesByTeam) {
    for (const entry of schedule) {
      const gameId = entry.id;
      if (!seen.has(gameId)) {
        seen.add(gameId);
        deduplicated.push(entry);
      }
    }
  }

  return deduplicated;
}

/**
 * Extracts schedule fields from a schedule event (team schedule endpoint).
 * Conforms to the cleaned schema: season, game_id, week, home_team_id, home_team, home_score, away_team_id, away_team, away_score, venue, completed, status.
 *
 * Note: Season must be provided by the caller (from context or environment).
 * Team names are included as denormalization.
 * Scores are extracted from {value, displayValue} objects.
 *
 * @param {any} event - A single event from the schedule endpoint
 * @param {string|number} season - The season year
 * @returns {object} Extracted schedule record
 */
function pluckScheduleFields(event, season) {
  const competition = event.competitions?.[0] || {};
  const competitors = competition.competitors || [];
  const homeTeam = competitors.find((c) => c.homeAway === 'home');
  const awayTeam = competitors.find((c) => c.homeAway === 'away');

  // Extract score values from {value, displayValue} objects
  const homeScore = homeTeam?.score?.value;
  const awayScore = awayTeam?.score?.value;

  return {
    season,
    game_id: event.id,
    date: competition.date,
    week: event.week?.number,
    home_team_id: homeTeam?.team?.id,
    home_team: homeTeam?.team?.displayName,
    home_score: homeScore,
    away_team_id: awayTeam?.team?.id,
    away_team: awayTeam?.team?.displayName,
    away_score: awayScore,
    venue: competition.venue?.fullName,
    completed: competition.status?.type === 'Final',
    status: competition.status?.type,
  };
}

/**
 * Extracts game header/venue metadata from a game summary response.
 * Conforms to the cleaned schema: game_id, venue, etc.
 *
 * @param {any} summaryData - The /summary?event={gameId} response
 * @param {string} gameId - The game ID (passed explicitly to ensure it's captured)
 * @returns {object} Extracted game record with venue and timestamp
 */
function pluckGameHeader(summaryData, gameId) {
  const competition = summaryData.header?.competitions?.[0];
  const gameInfo = summaryData.gameInfo;

  return {
    game_id: gameId,
    date: competition?.date,
    venue: gameInfo?.venue?.fullName,
    city: gameInfo?.venue?.address?.city,
    status: competition?.status?.type,
    completed: competition?.status?.type === 'Final',
  };
}

/**
 * Extracts team-level box score stats from a game summary.
 * Returns an array of two entries (home and away team stats).
 * Conforms to the cleaned schema: game_id, team_id, team_name, home_away, ...stats.
 *
 * @param {any} summaryData - The /summary?event={gameId} response
 * @param {string} gameId - The game ID to attach to each team stat record
 * @returns {any[]} Array of { game_id, team_id, team_name, team_abbr, home_away, ...stats } objects
 */
function pluckBoxscoreTeamStats(summaryData, gameId) {
  const teams = summaryData.boxscore?.teams || [];

  return teams.map((teamData, index) => {
    const team = teamData.team || {};
    const statistics = teamData.statistics || [];
    // Home team is index 0, away team is index 1 (ESPN convention)
    const homeAway = index === 0 ? 'H' : 'A';

    // Flatten statistics array into a key-value object
    const stats = {};
    for (const stat of statistics) {
      stats[stat.name] = stat.value;
      stats[`${stat.name}_display`] = stat.displayValue;
    }

    return {
      game_id: gameId,
      team_id: team.id,
      team_name: team.displayName,
      team_abbr: team.abbreviation,
      home_away: homeAway,
      ...stats,
    };
  });
}

/**
 * Determines which games need a summary fetch in this run.
 * Selects games that are:
 * - Not completed, OR
 * - Completed within the trailing window (catches stat corrections)
 * Caps the result to prevent runaway requests.
 *
 * @param {any[]} deduplicatedSchedules - Deduplicated schedule entries
 * @param {Date} now - Current timestamp (for relative calculations)
 * @returns {string[]} Array of game IDs to fetch summaries for
 */
function selectTargetGames(deduplicatedSchedules, now = new Date()) {
  const trailingDays = parseInt(process.env.GAME_SUMMARY_TRAILING_DAYS || '3', 10);
  const maxTargets = parseInt(process.env.GAME_SUMMARY_MAX_TARGETS || '50', 10);
  const trailingMs = trailingDays * 24 * 60 * 60 * 1000;
  const cutoff = new Date(now.getTime() - trailingMs);

  const targets = [];

  for (const game of deduplicatedSchedules) {
    const gameId = game.id;
    const completed = game.competitions?.[0]?.status?.type === 'Final';
    const gameDate = new Date(game.date);

    // Include: not completed OR completed recently
    if (!completed || gameDate >= cutoff) {
      targets.push(gameId);
    }

    if (targets.length >= maxTargets) {
      break;
    }
  }

  return targets;
}

module.exports = {
  dedupeSchedulesByGameId,
  pluckScheduleFields,
  pluckGameHeader,
  pluckBoxscoreTeamStats,
  selectTargetGames,
};

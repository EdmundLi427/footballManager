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
 * Extracts game header/venue metadata from a game summary response.
 * @param {any} summaryData - The /summary?event={gameId} response
 * @returns {object} Extracted game record with date, attendance, venue
 */
function pluckGameHeader(summaryData) {
  const competition = summaryData.header?.competitions?.[0];
  const gameInfo = summaryData.gameInfo;

  return {
    id: summaryData.header?.uid,
    date: competition?.date,
    status: competition?.status?.type,
    completed: competition?.status?.type === 'Final',
    venue: gameInfo?.venue?.fullName,
    city: gameInfo?.venue?.address?.city,
    attendance: gameInfo?.attendance,
  };
}

/**
 * Extracts team-level box score stats from a game summary.
 * Returns an array of two entries (home and away team stats).
 * @param {any} summaryData - The /summary?event={gameId} response
 * @returns {any[]} Array of { team, statistics } objects
 */
function pluckBoxscoreTeamStats(summaryData) {
  const teams = summaryData.boxscore?.teams || [];

  return teams.map((teamData) => {
    const team = teamData.team || {};
    const statistics = teamData.statistics || [];

    // Flatten statistics array into a key-value object
    const stats = {};
    for (const stat of statistics) {
      stats[stat.name] = stat.value;
      stats[`${stat.name}_display`] = stat.displayValue;
    }

    return {
      teamId: team.id,
      teamName: team.displayName,
      teamAbbr: team.abbreviation,
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
  pluckGameHeader,
  pluckBoxscoreTeamStats,
  selectTargetGames,
};

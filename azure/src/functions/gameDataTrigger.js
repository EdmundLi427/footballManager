const { app } = require('@azure/functions');
const { ESPN_SITES, getCurrentSeasonYear, fetchWithRetry, mapWithConcurrency } = require('../lib/espnClient');
const { dedupeSchedulesByGameId, pluckGameHeader, pluckBoxscoreTeamStats, selectTargetGames } = require('../lib/espnAssemble');
const { uploadJsonBlob, getStorageInfo } = require('../lib/gameDataStorage');

app.timer('fetchGameData', {
  schedule: '0 0 */6 * * *',
  handler: async (timer, context) => {
    const startTime = Date.now();
    const runTimestamp = new Date().toISOString();
    const seasonYear = getCurrentSeasonYear();

    const summary = {
      timestamp: runTimestamp,
      status: 'success',
      seasonYear,
      durationMs: 0,
      datasets: {},
      totalUploadedBytes: 0,
      failures: [],
    };

    try {
      context.log('[FETCH_START] Fetching game data (schedules, games, boxscores)');
      context.log(`[SEASON_RESOLVED] Season year: ${seasonYear}`);

      // Fetch team list
      context.log('[API_CALL] GET teams (for schedule IDs)');
      const teamsUrl = `${ESPN_SITES.SITE_BASE}/teams?limit=100`;
      const teamsResp = await fetchWithRetry(teamsUrl, {}, context);
      const teamsData = await teamsResp.json();
      const teamIds = (teamsData.sports?.[0]?.leagues?.[0]?.teams || []).map((t) => t.team?.id).filter(Boolean);

      context.log(`[DATA_PARSED] Found ${teamIds.length} teams for schedule fetch`);

      // Fetch schedules for all teams in parallel
      const concurrencyLimit = parseInt(process.env.ESPN_FETCH_CONCURRENCY || '5', 10);
      const scheduleResults = await mapWithConcurrency(teamIds, concurrencyLimit, async (teamId) => {
        try {
          const scheduleUrl = `${ESPN_SITES.SITE_BASE}/teams/${teamId}/schedule?season=${seasonYear}`;
          const scheduleResp = await fetchWithRetry(scheduleUrl, {}, context);
          const scheduleData = await scheduleResp.json();
          const events = scheduleData.events || [];

          // Extract relevant fields from each game
          return {
            teamId,
            events: events.map((event) => {
              const competition = event.competitions?.[0] || {};
              const competitors = competition.competitors || [];
              const homeTeam = competitors.find((c) => c.homeAway === 'home');
              const awayTeam = competitors.find((c) => c.homeAway === 'away');

              return {
                id: event.id,
                date: competition.date,
                week: event.week?.number,
                status: competition.status?.type,
                completed: competition.status?.type === 'Final',
                homeTeamId: homeTeam?.team?.id,
                awayTeamId: awayTeam?.team?.id,
                homeScore: homeTeam?.score,
                awayScore: awayTeam?.score,
                venue: competition.venue?.fullName,
              };
            }),
            error: null,
          };
        } catch (error) {
          return { teamId, events: [], error: error.message };
        }
      });

      // Aggregate schedules and track failures
      const allSchedules = [];
      let scheduleFailures = 0;

      for (const result of scheduleResults) {
        if (result.error) {
          summary.failures.push({ dataset: 'schedules', teamId: result.teamId, error: result.error });
          scheduleFailures++;
          context.log(`[ITEM_ERROR] Schedule fetch failed for team ${result.teamId}: ${result.error}`);
        } else {
          allSchedules.push(...result.events);
        }
      }

      const deduplicatedSchedules = dedupeSchedulesByGameId([allSchedules]);
      const scheduleStatus = scheduleFailures === teamIds.length ? 'failed' : scheduleFailures > 0 ? 'partial' : 'success';

      context.log(`[DATA_PARSED] Schedules: ${deduplicatedSchedules.length} unique games after deduping`);

      const schedulesUpload = await uploadJsonBlob('schedules', deduplicatedSchedules, context);
      summary.datasets.schedules = {
        status: scheduleStatus,
        teamFailures: scheduleFailures,
        uniqueGames: deduplicatedSchedules.length,
        blobName: schedulesUpload.blobName,
        bytes: schedulesUpload.bytes,
      };
      summary.totalUploadedBytes += schedulesUpload.bytes;

      // Determine which games need summary fetch (in-progress, not completed, or recently completed)
      const targetGameIds = selectTargetGames(deduplicatedSchedules);
      context.log(`[TARGET_GAMES] Selected ${targetGameIds.length} games for summary fetch`);

      // Fetch game summaries in parallel
      const summaryResults = await mapWithConcurrency(targetGameIds, concurrencyLimit, async (gameId) => {
        try {
          const summaryUrl = `${ESPN_SITES.SITE_BASE}/summary?event=${gameId}`;
          const summaryResp = await fetchWithRetry(summaryUrl, {}, context);
          const summaryData = await summaryResp.json();

          const gameHeader = pluckGameHeader(summaryData, gameId);
          const teamStats = pluckBoxscoreTeamStats(summaryData);

          return { gameId, gameHeader, teamStats, error: null };
        } catch (error) {
          return { gameId, gameHeader: null, teamStats: [], error: error.message };
        }
      });

      // Aggregate games and game-team-stats, track failures
      const gamesPayload = [];
      const gameTeamStatsPayload = [];
      let gameSummaryFailures = 0;

      for (const result of summaryResults) {
        if (result.error) {
          summary.failures.push({ dataset: 'games', gameId: result.gameId, error: result.error });
          gameSummaryFailures++;
          context.log(`[ITEM_ERROR] Summary fetch failed for game ${result.gameId}: ${result.error}`);
        } else {
          gamesPayload.push(result.gameHeader);
          gameTeamStatsPayload.push(...result.teamStats);
        }
      }

      const gamesStatus = gameSummaryFailures === targetGameIds.length ? 'failed' : gameSummaryFailures > 0 ? 'partial' : 'success';

      context.log(`[DATASET_SUMMARY] Games: ${gamesPayload.length} games, ${gameSummaryFailures} failures`);
      context.log(`[DATASET_SUMMARY] Game team stats: ${gameTeamStatsPayload.length} team records`);

      const gamesUpload = await uploadJsonBlob('games', gamesPayload, context);
      summary.datasets.games = {
        status: gamesStatus,
        count: gamesPayload.length,
        itemFailures: gameSummaryFailures,
        blobName: gamesUpload.blobName,
        bytes: gamesUpload.bytes,
      };
      summary.totalUploadedBytes += gamesUpload.bytes;

      const gameTeamStatsUpload = await uploadJsonBlob('game-team-stats', gameTeamStatsPayload, context);
      summary.datasets['game-team-stats'] = {
        status: gamesStatus,
        count: gameTeamStatsPayload.length,
        itemFailures: gameSummaryFailures,
        blobName: gameTeamStatsUpload.blobName,
        bytes: gameTeamStatsUpload.bytes,
      };
      summary.totalUploadedBytes += gameTeamStatsUpload.bytes;

      // Determine overall status: fail only if all datasets failed
      const allStatuses = Object.values(summary.datasets).map((ds) => ds.status);
      if (allStatuses.every((s) => s === 'failed')) {
        summary.status = 'failed';
      } else if (allStatuses.some((s) => s === 'failed' || s === 'partial')) {
        summary.status = 'partial';
      }

      summary.durationMs = Date.now() - startTime;

      const storageInfo = getStorageInfo();
      context.log(`[STORAGE_INFO] Account: ${storageInfo.accountName}, Container: ${storageInfo.containerName}`);
      context.log(`[SUMMARY] ${JSON.stringify(summary)}`);

      if (summary.status === 'failed') {
        throw new Error('All datasets failed; check logs for details');
      }

      return summary;
    } catch (error) {
      summary.status = 'error';
      summary.error = error.message;
      summary.durationMs = Date.now() - startTime;
      context.error(`[ERROR] ${error.message}`);
      context.error(`[SUMMARY] ${JSON.stringify(summary)}`);
      throw error;
    }
  },
});
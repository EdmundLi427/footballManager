const { app } = require('@azure/functions');
const { ESPN_SITES, getCurrentSeasonYear, fetchWithRetry, mapWithConcurrency } = require('../lib/espnClient');
const {
  BLOB_PREFIXES,
  dedupeSchedulesByGameId,
  trimScheduleEvent,
  buildGameSummaryLanding,
  selectTargetGames,
} = require('../lib/espnAssemble');
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
      context.log('[FETCH_START] Fetching game data (schedules, game summaries)');
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
          const events = (scheduleData.events || []).map(trimScheduleEvent);

          return { teamId, events, error: null };
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

      const schedulesUpload = await uploadJsonBlob(BLOB_PREFIXES.schedules, deduplicatedSchedules, context);
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

          return { gameId, landing: buildGameSummaryLanding(summaryData, gameId), error: null };
        } catch (error) {
          return { gameId, landing: null, error: error.message };
        }
      });

      // Aggregate game summaries, track failures
      const gameSummariesPayload = [];
      let gameSummaryFailures = 0;

      for (const result of summaryResults) {
        if (result.error) {
          summary.failures.push({ dataset: 'game_summaries', gameId: result.gameId, error: result.error });
          gameSummaryFailures++;
          context.log(`[ITEM_ERROR] Summary fetch failed for game ${result.gameId}: ${result.error}`);
        } else {
          gameSummariesPayload.push(result.landing);
        }
      }

      const summariesStatus =
        targetGameIds.length > 0 && gameSummaryFailures === targetGameIds.length
          ? 'failed'
          : gameSummaryFailures > 0
            ? 'partial'
            : 'success';

      context.log(`[DATASET_SUMMARY] Game summaries: ${gameSummariesPayload.length} games, ${gameSummaryFailures} failures`);

      if (gameSummariesPayload.length > 0) {
        const summariesUpload = await uploadJsonBlob(BLOB_PREFIXES.gameSummaries, gameSummariesPayload, context);
        summary.datasets.game_summaries = {
          status: summariesStatus,
          count: gameSummariesPayload.length,
          itemFailures: gameSummaryFailures,
          blobName: summariesUpload.blobName,
          bytes: summariesUpload.bytes,
        };
        summary.totalUploadedBytes += summariesUpload.bytes;
      } else {
        summary.datasets.game_summaries = { status: summariesStatus, count: 0, itemFailures: gameSummaryFailures };
      }

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
const { app } = require('@azure/functions');
const { getCurrentSeasonYear } = require('../lib/espnClient');
const { BLOB_PREFIXES, selectTargetGames } = require('../lib/espnAssemble');
const { fetchTeamIds, fetchSeasonSchedules, fetchGameSummaries } = require('../lib/espnFetch');
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

      const teamIds = await fetchTeamIds(context);
      context.log(`[DATA_PARSED] Found ${teamIds.length} teams for schedule fetch`);

      // Regular season + playoffs; each game appears in both teams' schedules and is deduped
      const { events: deduplicatedSchedules, failures: scheduleFailureList } = await fetchSeasonSchedules(
        teamIds,
        seasonYear,
        context
      );

      for (const failure of scheduleFailureList) {
        summary.failures.push({ dataset: 'schedules', ...failure });
        context.log(
          `[ITEM_ERROR] Schedule fetch failed for team ${failure.teamId} seasontype ${failure.seasonType}: ${failure.error}`
        );
      }

      const scheduleRequests = teamIds.length * 2;
      const scheduleFailures = scheduleFailureList.length;
      const scheduleStatus =
        scheduleFailures === scheduleRequests ? 'failed' : scheduleFailures > 0 ? 'partial' : 'success';

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

      const { landings: gameSummariesPayload, failures: summaryFailureList } = await fetchGameSummaries(
        targetGameIds,
        context
      );

      for (const failure of summaryFailureList) {
        summary.failures.push({ dataset: 'game_summaries', ...failure });
        context.log(`[ITEM_ERROR] Summary fetch failed for game ${failure.gameId}: ${failure.error}`);
      }
      const gameSummaryFailures = summaryFailureList.length;

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
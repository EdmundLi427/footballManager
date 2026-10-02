const { app } = require('@azure/functions');
const { ESPN_SITES, getCurrentSeasonYear, fetchWithRetry, mapWithConcurrency } = require('../lib/espnClient');
const { uploadJsonBlob, getStorageInfo } = require('../lib/gameDataStorage');
const { BLOB_PREFIXES, buildStandingsLanding, buildRosterLanding } = require('../lib/espnAssemble');

app.timer('fetchStandingsRosters', {
  schedule: '0 30 8 * * *',
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
      context.log('[FETCH_START] Fetching standings and rosters data');
      context.log(`[SEASON_RESOLVED] Season year: ${seasonYear}`);

      // Fetch standings
      context.log('[API_CALL] GET standings');
      const standingsUrl = `${ESPN_SITES.STANDINGS_URL}?season=${seasonYear}`;
      const standingsResp = await fetchWithRetry(standingsUrl, {}, context);
      const standingsData = await standingsResp.json();

      const standingsLanding = buildStandingsLanding(standingsData);
      const standingsEntryCount = standingsLanding.children.reduce(
        (n, child) => n + child.standings.entries.length,
        0
      );

      context.log(`[DATA_PARSED] Standings: ${standingsEntryCount} team entries`);
      if (standingsEntryCount === 0) {
        throw new Error('Standings response contained no entries; ESPN shape may have changed');
      }

      const standingsUpload = await uploadJsonBlob(BLOB_PREFIXES.standings, [standingsLanding], context);
      summary.datasets.standings = {
        status: 'success',
        count: standingsEntryCount,
        blobName: standingsUpload.blobName,
        bytes: standingsUpload.bytes,
      };
      summary.totalUploadedBytes += standingsUpload.bytes;

      // Fetch team list to get IDs for roster calls
      context.log('[API_CALL] GET teams (for roster IDs)');
      const teamsUrl = `${ESPN_SITES.SITE_BASE}/teams?limit=100`;
      const teamsResp = await fetchWithRetry(teamsUrl, {}, context);
      const teamsData = await teamsResp.json();
      const teamIds = (teamsData.sports?.[0]?.leagues?.[0]?.teams || []).map((t) => t.team?.id).filter(Boolean);

      context.log(`[DATA_PARSED] Found ${teamIds.length} teams for roster fetch`);

      // Fetch rosters in parallel with concurrency limit
      const concurrencyLimit = parseInt(process.env.ESPN_FETCH_CONCURRENCY || '5', 10);
      const rosterResults = await mapWithConcurrency(teamIds, concurrencyLimit, async (teamId) => {
        try {
          const rosterUrl = `${ESPN_SITES.SITE_BASE}/teams/${teamId}/roster?season=${seasonYear}`;
          const rosterResp = await fetchWithRetry(rosterUrl, {}, context);
          const rosterData = await rosterResp.json();

          return { teamId, landing: buildRosterLanding(rosterData), error: null };
        } catch (error) {
          return { teamId, landing: null, error: error.message };
        }
      });

      // Aggregate rosters and track failures
      const rostersPayload = [];
      let rosterFailures = 0;

      for (const result of rosterResults) {
        if (result.error) {
          summary.failures.push({ dataset: 'rosters', teamId: result.teamId, error: result.error });
          rosterFailures++;
          context.log(`[ITEM_ERROR] Roster fetch failed for team ${result.teamId}: ${result.error}`);
        } else {
          rostersPayload.push(result.landing);
        }
      }

      const rosterStatus = rosterFailures === teamIds.length ? 'failed' : rosterFailures > 0 ? 'partial' : 'success';
      const playerCount = rostersPayload.reduce(
        (n, roster) => n + roster.athletes.reduce((m, group) => m + group.items.length, 0),
        0
      );
      context.log(`[DATASET_SUMMARY] Rosters: ${rostersPayload.length} teams, ${playerCount} players, ${rosterFailures} team failures`);

      const rostersUpload = await uploadJsonBlob(BLOB_PREFIXES.rosters, rostersPayload, context);
      summary.datasets.rosters = {
        status: rosterStatus,
        teamCount: teamIds.length,
        itemFailures: rosterFailures,
        playerCount,
        blobName: rostersUpload.blobName,
        bytes: rostersUpload.bytes,
      };
      summary.totalUploadedBytes += rostersUpload.bytes;

      // If all rosters failed, fail the whole invocation; otherwise partial/success
      if (summary.datasets.rosters.status === 'failed') {
        summary.status = 'failed';
      } else if (
        Object.values(summary.datasets).some((ds) => ds.status === 'partial') ||
        summary.failures.length > 0
      ) {
        summary.status = 'partial';
      }

      summary.durationMs = Date.now() - startTime;

      const storageInfo = getStorageInfo();
      context.log(`[STORAGE_INFO] Account: ${storageInfo.accountName}, Container: ${storageInfo.containerName}`);
      context.log(`[SUMMARY] ${JSON.stringify(summary)}`);

      // Only throw if all datasets failed
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

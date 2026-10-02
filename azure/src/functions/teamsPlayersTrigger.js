const { app } = require('@azure/functions');
const { ESPN_SITES, getCurrentSeasonYear, fetchWithRetry, paginateEspnEndpoint } = require('../lib/espnClient');
const { uploadJsonBlob, getStorageInfo } = require('../lib/gameDataStorage');
const { BLOB_PREFIXES, buildTeamsLanding, buildPlayersLanding } = require('../lib/espnAssemble');

app.timer('fetchTeamsPlayers', {
  schedule: '0 15 6 * * *',
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
      context.log('[FETCH_START] Fetching teams and players data');
      context.log(`[SEASON_RESOLVED] Season year: ${seasonYear}`);

      // Fetch teams
      context.log('[API_CALL] GET teams');
      const teamsUrl = `${ESPN_SITES.SITE_BASE}/teams?limit=100`;
      const teamsResp = await fetchWithRetry(teamsUrl, {}, context);
      const teamsData = await teamsResp.json();
      const teamsPayload = buildTeamsLanding(teamsData);

      context.log(`[DATA_PARSED] Teams: ${teamsPayload.length} received`);

      const teamsUpload = await uploadJsonBlob(BLOB_PREFIXES.teams, teamsPayload, context);
      summary.datasets.teams = {
        status: 'success',
        count: teamsPayload.length,
        blobName: teamsUpload.blobName,
        bytes: teamsUpload.bytes,
      };
      summary.totalUploadedBytes += teamsUpload.bytes;

      // Fetch players (paginated)
      context.log('[API_CALL] GET athletes (paginated)');
      const athletesUrl = `${ESPN_SITES.CORE_BASE}/seasons/${seasonYear}/athletes?limit=1000`;
      const athletesResp = await fetchWithRetry(athletesUrl, {}, context);
      const athletesInitial = await athletesResp.json();
      const allAthletes = await paginateEspnEndpoint(athletesUrl, context, athletesInitial);

      context.log(`[DATA_PARSED] Athletes: ${allAthletes.length} total across all pages`);

      const playersPayload = buildPlayersLanding(allAthletes);

      const playersUpload = await uploadJsonBlob(BLOB_PREFIXES.players, playersPayload, context);
      summary.datasets.players = {
        status: 'success',
        count: playersPayload.length,
        blobName: playersUpload.blobName,
        bytes: playersUpload.bytes,
      };
      summary.totalUploadedBytes += playersUpload.bytes;

      summary.status = 'success';
      summary.durationMs = Date.now() - startTime;

      const storageInfo = getStorageInfo();
      context.log(`[STORAGE_INFO] Account: ${storageInfo.accountName}, Container: ${storageInfo.containerName}`);
      context.log(`[SUMMARY] ${JSON.stringify(summary)}`);
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

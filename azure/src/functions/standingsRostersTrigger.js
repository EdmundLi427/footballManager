const { app } = require('@azure/functions');
const { ESPN_SITES, getCurrentSeasonYear, fetchWithRetry, mapWithConcurrency } = require('../lib/espnClient');
const { uploadJsonBlob, getStorageInfo } = require('../lib/gameDataStorage');

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

      // Flatten standings groups recursively (ESPN nests divisions/conferences)
      const standingsPayload = [];
      const flattenStandingsGroup = (group, season, conference = null) => {
        const groups = group.groups || [];
        for (const subgroup of groups) {
          const entries = subgroup.entries || [];
          for (const entry of entries) {
            const teamStats = {};
            const stats = entry.stats || [];
            for (const stat of stats) {
              teamStats[stat.name] = stat.value;
            }

            standingsPayload.push({
              teamId: entry.team?.id,
              teamName: entry.team?.displayName,
              season,
              conference: subgroup.name,
              ...teamStats,
            });
          }
        }
      };

      if (standingsData.children) {
        for (const group of standingsData.children) {
          flattenStandingsGroup(group, seasonYear);
        }
      }

      context.log(`[DATA_PARSED] Standings: ${standingsPayload.length} team entries`);

      const standingsUpload = await uploadJsonBlob('standings', standingsPayload, context);
      summary.datasets.standings = {
        status: 'success',
        count: standingsPayload.length,
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
          const athletes = rosterData.athletes || [];

          // Extract player data grouped by position
          const players = [];
          for (const positionGroup of athletes) {
            const position = positionGroup.position?.name || 'Unknown';
            const items = positionGroup.items || [];
            for (const player of items) {
              players.push({
                teamId,
                position,
                playerId: player.id,
                playerName: player.fullName,
                jersey: player.jersey,
                age: player.age,
                height: player.height,
                weight: player.weight,
                experience: player.experience,
              });
            }
          }

          return { teamId, players, error: null };
        } catch (error) {
          return { teamId, players: [], error: error.message };
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
          rostersPayload.push(...result.players);
        }
      }

      const rosterStatus = rosterFailures === teamIds.length ? 'failed' : rosterFailures > 0 ? 'partial' : 'success';
      context.log(`[DATASET_SUMMARY] Rosters: ${rostersPayload.length} players, ${rosterFailures} team failures`);

      const rostersUpload = await uploadJsonBlob('rosters', rostersPayload, context);
      summary.datasets.rosters = {
        status: rosterStatus,
        teamCount: teamIds.length,
        itemFailures: rosterFailures,
        playerCount: rostersPayload.length,
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

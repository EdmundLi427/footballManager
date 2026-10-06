#!/usr/bin/env node
/**
 * Lands whole seasons of ESPN schedules, game summaries and standings in blob storage, in exactly
 * the format and under the same prefixes the timer triggers use, so the Databricks Auto Loader
 * notebooks ingest them unchanged (ingest_game_schedules, _games, _team_stats, _standings).
 *
 *   node scripts/backfill-espn.js [--seasons 2023,2024,2025,2026] [--dry-run]
 *
 * Default seasons: the last 3 completed seasons plus the current one. Rosters are not backfilled
 * (ESPN's site roster endpoint returns empty groups for past seasons); teams/players are not
 * per-season, so the daily trigger snapshots already cover them.
 *
 * Re-runnable: the Delta merge is keyed, so re-landing a season only refreshes its rows.
 * Storage connection comes from env or azure/local.settings.json (never logged).
 */
const fs = require('node:fs');
const path = require('node:path');

const SUMMARY_CHUNK_SIZE = 100;

function loadLocalSettings() {
  const settings = path.join(__dirname, '..', 'local.settings.json');
  if (!fs.existsSync(settings)) return;
  const values = JSON.parse(fs.readFileSync(settings, 'utf8')).Values || {};
  for (const key of ['NEWS_STORAGE_CONNECTION', 'AzureWebJobsStorage', 'ALSOURCE_CONTAINER', 'ESPN_FETCH_CONCURRENCY']) {
    if (!process.env[key] && values[key]) process.env[key] = values[key];
  }
}

function parseArgs(argv, currentSeason) {
  const args = { seasons: [currentSeason - 3, currentSeason - 2, currentSeason - 1, currentSeason], dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--dry-run') args.dryRun = true;
    else if (argv[i] === '--seasons') args.seasons = argv[++i].split(',').map((s) => parseInt(s, 10));
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (args.seasons.some(Number.isNaN)) throw new Error('--seasons must be comma-separated years');
  return args;
}

function chunk(items, size) {
  const out = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

async function main() {
  loadLocalSettings();

  // Loaded after local settings populate process.env.
  const { getCurrentSeasonYear } = require('../src/lib/espnClient');
  const { BLOB_PREFIXES, selectCompletedGames } = require('../src/lib/espnAssemble');
  const { fetchTeamIds, fetchSeasonSchedules, fetchGameSummaries, fetchStandings } = require('../src/lib/espnFetch');
  const { uploadJsonBlob, getStorageInfo } = require('../src/lib/gameDataStorage');

  const { seasons, dryRun } = parseArgs(process.argv.slice(2), getCurrentSeasonYear());
  const context = { log: console.log, error: console.error };

  if (!dryRun && !process.env.NEWS_STORAGE_CONNECTION && !process.env.AzureWebJobsStorage) {
    throw new Error('No storage connection: set NEWS_STORAGE_CONNECTION or AzureWebJobsStorage (env or local.settings.json)');
  }

  const upload = async (prefix, data) => {
    const bytes = Buffer.byteLength(JSON.stringify(data));
    if (dryRun) {
      context.log(`[DRY_RUN] would upload ${prefix} (${data.length} records, ${bytes} bytes)`);
      return bytes;
    }
    return (await uploadJsonBlob(prefix, data, context)).bytes;
  };

  context.log(`[FETCH_START] Backfilling seasons ${seasons.join(', ')}${dryRun ? ' (dry run)' : ''}`);
  if (!dryRun) {
    const storageInfo = getStorageInfo();
    context.log(`[STORAGE_INFO] Account: ${storageInfo.accountName}, Container: ${storageInfo.containerName}`);
  }

  const teamIds = await fetchTeamIds(context);
  context.log(`[DATA_PARSED] Found ${teamIds.length} teams`);

  const report = [];

  for (const season of seasons) {
    const result = { season, games: 0, summaries: 0, standingsEntries: 0, bytes: 0, failures: [] };
    report.push(result);

    // Schedules (regular season + playoffs)
    const schedules = await fetchSeasonSchedules(teamIds, season, context);
    result.failures.push(...schedules.failures.map((f) => ({ dataset: 'schedules', ...f })));
    result.games = schedules.events.length;
    context.log(`[DATA_PARSED] ${season} schedules: ${schedules.events.length} unique games`);
    if (schedules.events.length > 0) {
      result.bytes += await upload(BLOB_PREFIXES.schedules, schedules.events);
    }

    // Game summaries for every completed game, chunked to keep blobs a reasonable size
    const gameIds = selectCompletedGames(schedules.events);
    context.log(`[TARGET_GAMES] ${season}: ${gameIds.length} completed games`);
    const summaries = await fetchGameSummaries(gameIds, context);
    result.failures.push(...summaries.failures.map((f) => ({ dataset: 'game_summaries', ...f })));
    result.summaries = summaries.landings.length;
    for (const part of chunk(summaries.landings, SUMMARY_CHUNK_SIZE)) {
      result.bytes += await upload(BLOB_PREFIXES.gameSummaries, part);
    }

    // Standings: regular season and playoffs land as separate (season, season_type) rows
    for (const seasonType of [2, 3]) {
      try {
        const landing = await fetchStandings(season, seasonType, context);
        const entries = landing.children.reduce((n, child) => n + child.standings.entries.length, 0);
        if (entries === 0) {
          context.log(`[DATA_PARSED] ${season} standings seasontype ${seasonType}: no entries, skipped`);
          continue;
        }
        result.standingsEntries += entries;
        result.bytes += await upload(BLOB_PREFIXES.standings, [landing]);
      } catch (error) {
        result.failures.push({ dataset: 'standings', seasonType, error: error.message });
      }
    }

    for (const failure of result.failures) {
      context.log(`[ITEM_ERROR] ${season} ${JSON.stringify(failure)}`);
    }
    context.log(
      `[DATASET_SUMMARY] ${season}: ${result.games} games, ${result.summaries}/${gameIds.length} summaries, ` +
        `${result.standingsEntries} standings entries, ${result.bytes} bytes, ${result.failures.length} failures`
    );
  }

  context.log(
    `[SUMMARY] ${JSON.stringify(report.map(({ failures, ...rest }) => ({ ...rest, failures: failures.length })))}`
  );

  if (report.some((r) => r.failures.length > 0)) {
    context.error('[ERROR] Some items failed; re-run the affected seasons with --seasons');
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`[ERROR] ${error.message}`);
  process.exit(1);
});

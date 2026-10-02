#!/usr/bin/env node
/**
 * Records small real ESPN responses and the blob payloads our landing builders produce from them.
 *
 *   node scripts/refresh-espn-fixtures.js
 *
 * Writes:
 *   azure/test/fixture/espn/*.json            raw ESPN responses (trimmed to a few records)
 *   databricks/tests/fixtures/espn/*.json     exact blob payloads = Databricks parser inputs
 *
 * Re-run it to detect ESPN schema drift: any diff in the raw fixtures means ESPN changed shape,
 * and the Databricks tests will show whether the parsers still cope.
 *
 * Also records a FantasyPros injury snapshot (stored by newsTrigger unchanged) into
 * databricks/tests/fixtures/fantasypros/injury_news.json when FANTASY_PRO_API_KEY is set in the
 * environment or azure/local.settings.json; skipped otherwise. The key is never logged.
 *
 * Env: FIXTURE_TEAM_ID (default 12 = KC), FIXTURE_GAME_ID (default 401872931 = DEN @ KC, 2026 wk 1).
 */
const fs = require('node:fs');
const path = require('node:path');
const { ESPN_SITES, getCurrentSeasonYear } = require('../src/lib/espnClient');
const {
  buildTeamsLanding,
  buildPlayersLanding,
  buildRosterLanding,
  buildStandingsLanding,
  trimScheduleEvent,
  buildGameSummaryLanding,
} = require('../src/lib/espnAssemble');

const TEAM_ID = process.env.FIXTURE_TEAM_ID || '12';
const GAME_ID = process.env.FIXTURE_GAME_ID || '401872931';
const SEASON = getCurrentSeasonYear();

const RAW_DIR = path.join(__dirname, '..', 'test', 'fixture', 'espn');
const LANDING_DIR = path.join(__dirname, '..', '..', 'databricks', 'tests', 'fixtures', 'espn');
const FANTASYPROS_DIR = path.join(__dirname, '..', '..', 'databricks', 'tests', 'fixtures', 'fantasypros');
const FANTASYPROS_NEWS_URL = 'https://api.fantasypros.com/public/v2/json/nfl/news?category=injury&pageIndex=1';

function fantasyProsKey() {
  if (process.env.FANTASY_PRO_API_KEY) return process.env.FANTASY_PRO_API_KEY;
  const settings = path.join(__dirname, '..', 'local.settings.json');
  if (!fs.existsSync(settings)) return null;
  return JSON.parse(fs.readFileSync(settings, 'utf8')).Values?.FANTASY_PRO_API_KEY || null;
}

async function getJson(url, headers = {}) {
  const resp = await fetch(url, { headers });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText} for ${url}`);
  return resp.json();
}

function write(dir, name, data) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${name}.json`);
  fs.writeFileSync(file, `${JSON.stringify(data, null, 2)}\n`);
  console.log(`wrote ${path.relative(process.cwd(), file)}`);
}

/** Keeps a completed, an unplayed, and (if any) an in-progress event so every branch is covered. */
function sampleEvents(events) {
  const byState = (state) => events.find((e) => e.competitions?.[0]?.status?.type?.state === state);
  return [byState('post'), byState('in'), byState('pre')].filter(Boolean);
}

async function main() {
  const teams = await getJson(`${ESPN_SITES.SITE_BASE}/teams?limit=100`);
  teams.sports[0].leagues[0].teams = teams.sports[0].leagues[0].teams.slice(0, 3);

  const athletes = await getJson(`${ESPN_SITES.CORE_BASE}/seasons/${SEASON}/athletes?limit=3`);

  const roster = await getJson(`${ESPN_SITES.SITE_BASE}/teams/${TEAM_ID}/roster?season=${SEASON}`);
  roster.athletes = roster.athletes.map((group) => ({ ...group, items: group.items.slice(0, 2) }));

  const standings = await getJson(`${ESPN_SITES.STANDINGS_URL}?season=${SEASON}`);
  for (const child of standings.children) {
    child.standings.entries = child.standings.entries.slice(0, 2);
  }

  const schedule = await getJson(`${ESPN_SITES.SITE_BASE}/teams/${TEAM_ID}/schedule?season=${SEASON}`);
  schedule.events = sampleEvents(schedule.events);

  const summaryFull = await getJson(`${ESPN_SITES.SITE_BASE}/summary?event=${GAME_ID}`);
  const summary = {
    header: summaryFull.header,
    gameInfo: summaryFull.gameInfo,
    boxscore: { teams: summaryFull.boxscore.teams },
  };

  write(RAW_DIR, 'teams', teams);
  write(RAW_DIR, 'athletes', athletes);
  write(RAW_DIR, 'roster', roster);
  write(RAW_DIR, 'standings', standings);
  write(RAW_DIR, 'schedule', schedule);
  write(RAW_DIR, 'summary', { game_id: GAME_ID, ...summary });

  write(LANDING_DIR, 'teams', buildTeamsLanding(teams));
  write(LANDING_DIR, 'players', buildPlayersLanding(athletes.items));
  write(LANDING_DIR, 'rosters', [buildRosterLanding(roster)]);
  write(LANDING_DIR, 'standings', [buildStandingsLanding(standings)]);
  write(LANDING_DIR, 'schedules', schedule.events.map(trimScheduleEvent));
  write(LANDING_DIR, 'game_summaries', [buildGameSummaryLanding(summary, GAME_ID)]);

  const apiKey = fantasyProsKey();
  if (!apiKey) {
    console.log('skipped FantasyPros injury fixture: FANTASY_PRO_API_KEY not set');
    return;
  }
  const news = await getJson(FANTASYPROS_NEWS_URL, { 'x-api-key': apiKey });
  news.items = (news.items || []).slice(0, 3);
  write(FANTASYPROS_DIR, 'injury_news', news);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

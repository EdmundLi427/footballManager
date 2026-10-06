const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');
const {
  BLOB_PREFIXES,
  dedupeSchedulesByGameId,
  buildTeamsLanding,
  buildPlayersLanding,
  buildRosterLanding,
  buildStandingsLanding,
  trimScheduleEvent,
  buildGameSummaryLanding,
  selectTargetGames,
  selectCompletedGames,
} = require('../../src/lib/espnAssemble');

const RAW_DIR = path.join(__dirname, '..', 'fixture', 'espn');
const LANDING_DIR = path.join(__dirname, '..', '..', '..', 'databricks', 'tests', 'fixtures', 'espn');

const loadRaw = (name) => JSON.parse(fs.readFileSync(path.join(RAW_DIR, `${name}.json`), 'utf8'));
const loadLanding = (name) => JSON.parse(fs.readFileSync(path.join(LANDING_DIR, `${name}.json`), 'utf8'));

// Contract: builder(recorded ESPN response) must equal the fixture the Databricks parsers are tested on.
test('landing payloads match the Databricks parser fixtures', async (t) => {
  await t.test('teams', () => {
    assert.deepStrictEqual(buildTeamsLanding(loadRaw('teams')), loadLanding('teams'));
  });

  await t.test('players', () => {
    assert.deepStrictEqual(buildPlayersLanding(loadRaw('athletes').items), loadLanding('players'));
  });

  await t.test('rosters', () => {
    assert.deepStrictEqual([buildRosterLanding(loadRaw('roster'))], loadLanding('rosters'));
  });

  await t.test('standings', () => {
    assert.deepStrictEqual([buildStandingsLanding(loadRaw('standings'))], loadLanding('standings'));
  });

  await t.test('schedules', () => {
    assert.deepStrictEqual(loadRaw('schedule').events.map(trimScheduleEvent), loadLanding('schedules'));
  });

  await t.test('game summaries', () => {
    const { game_id: gameId, ...summary } = loadRaw('summary');
    assert.deepStrictEqual([buildGameSummaryLanding(summary, gameId)], loadLanding('game_summaries'));
  });
});

test('landing builders keep the ESPN fields the parsers read', async (t) => {
  await t.test('game summary keeps real homeAway (ESPN lists the away team first)', () => {
    const [landing] = loadLanding('game_summaries');
    const sides = landing.boxscore.teams.map((team) => team.homeAway).sort();
    assert.deepStrictEqual(sides, ['away', 'home']);
    for (const team of landing.boxscore.teams) {
      assert.ok(team.statistics.every((stat) => typeof stat.displayValue === 'string'));
    }
  });

  await t.test('schedule events keep status object and competitor scores', () => {
    for (const event of loadLanding('schedules')) {
      const competition = event.competitions[0];
      assert.strictEqual(typeof competition.status.type.completed, 'boolean');
      assert.strictEqual(competition.competitors.length, 2);
      assert.ok(competition.competitors.every((c) => c.team.id && c.homeAway));
      assert.ok(!('leaders' in competition.competitors[0]));
    }
  });

  await t.test('standings entries are present (children[].standings.entries)', () => {
    const [landing] = loadLanding('standings');
    assert.ok(landing.children.length > 0);
    assert.ok(landing.children.every((child) => child.standings.entries.length > 0));
  });

  await t.test('rosters drop heavy subtrees but keep position objects', () => {
    const [landing] = loadLanding('rosters');
    const player = landing.athletes[0].items[0];
    assert.ok(player.position.abbreviation);
    assert.ok(!('headshot' in player));
    assert.ok(!('links' in player));
  });

  await t.test('blob prefixes all live under espn/', () => {
    assert.ok(Object.values(BLOB_PREFIXES).every((prefix) => prefix.startsWith('espn/')));
  });
});

test('dedupeSchedulesByGameId', async (t) => {
  await t.test('deduplicates games that appear in multiple team schedules', () => {
    const result = dedupeSchedulesByGameId([
      [{ id: '1' }, { id: '2' }],
      [{ id: '2' }, { id: '3' }],
    ]);
    assert.deepStrictEqual(
      result.map((g) => g.id),
      ['1', '2', '3']
    );
  });

  await t.test('preserves first occurrence', () => {
    const result = dedupeSchedulesByGameId([[{ id: '1', data: 'first' }], [{ id: '1', data: 'second' }]]);
    assert.strictEqual(result[0].data, 'first');
  });
});

const event = (id, date, completed) => ({
  id,
  date,
  competitions: [{ date, status: { type: { completed, state: completed ? 'post' : 'pre' } } }],
});

test('selectTargetGames', async (t) => {
  const now = new Date('2026-10-05T00:00Z');

  await t.test('selects started, not-completed games and skips future games', () => {
    const result = selectTargetGames(
      [event('in_progress', '2026-10-04T20:00Z', false), event('future', '2026-10-12T20:00Z', false)],
      now
    );
    assert.deepStrictEqual(result, ['in_progress']);
  });

  await t.test('includes completed games only inside the trailing window', () => {
    const result = selectTargetGames(
      [event('recent', '2026-10-04T20:00Z', true), event('old', '2026-09-27T20:00Z', true)],
      now
    );
    assert.deepStrictEqual(result, ['recent']);
  });

  await t.test('reads completion from the real ESPN status object', () => {
    const [completed] = loadLanding('schedules').filter((e) => e.competitions[0].status.type.completed);
    const longAfter = new Date(new Date(completed.competitions[0].date).getTime() + 30 * 86400000);
    assert.deepStrictEqual(selectTargetGames([completed], longAfter), []);
  });

  await t.test('respects max targets ceiling', () => {
    process.env.GAME_SUMMARY_MAX_TARGETS = '2';
    const events = Array.from({ length: 10 }, (_, i) => event(`g${i}`, '2026-10-04T20:00Z', false));
    assert.strictEqual(selectTargetGames(events, now).length, 2);
    delete process.env.GAME_SUMMARY_MAX_TARGETS;
  });
});

test('selectCompletedGames', async (t) => {
  const now = new Date('2026-10-05T00:00Z');

  await t.test('selects every completed game regardless of age, skipping unfinished and future games', () => {
    const result = selectCompletedGames(
      [
        event('old', '2023-09-10T20:00Z', true),
        event('recent', '2026-10-04T20:00Z', true),
        event('in_progress', '2026-10-04T23:00Z', false),
        event('future', '2026-10-12T20:00Z', false),
      ],
      now
    );
    assert.deepStrictEqual(result, ['old', 'recent']);
  });

  await t.test('is not capped by GAME_SUMMARY_MAX_TARGETS', () => {
    process.env.GAME_SUMMARY_MAX_TARGETS = '2';
    const events = Array.from({ length: 10 }, (_, i) => event(`g${i}`, '2024-10-04T20:00Z', true));
    assert.strictEqual(selectCompletedGames(events, now).length, 10);
    delete process.env.GAME_SUMMARY_MAX_TARGETS;
  });
});

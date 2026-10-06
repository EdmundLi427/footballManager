const test = require('node:test');
const assert = require('node:assert');
const { fetchSeasonSchedules, fetchGameSummaries } = require('../../src/lib/espnFetch');

const mockContext = { log: () => {}, error: () => {} };
const ok = (body) => ({ ok: true, json: async () => body });
const event = (id) => ({ id, competitions: [{ competitors: [] }] });

test('fetchSeasonSchedules', async (t) => {
  t.after(() => delete process.env.ESPN_FETCH_RETRIES);
  process.env.ESPN_FETCH_RETRIES = '1';

  await t.test('requests regular season and playoffs for every team', async () => {
    const urls = [];
    global.fetch = async (url) => {
      urls.push(url);
      return ok({ events: [] });
    };

    await fetchSeasonSchedules(['1', '2'], 2024, mockContext);
    assert.deepStrictEqual(
      urls.map((u) => u.split('/teams/')[1]).sort(),
      [
        '1/schedule?season=2024&seasontype=2',
        '1/schedule?season=2024&seasontype=3',
        '2/schedule?season=2024&seasontype=2',
        '2/schedule?season=2024&seasontype=3',
      ]
    );
  });

  await t.test('dedupes games across teams and season types', async () => {
    global.fetch = async (url) => {
      if (url.includes('/teams/1/') && url.endsWith('seasontype=2')) return ok({ events: [event('a'), event('b')] });
      if (url.includes('/teams/2/') && url.endsWith('seasontype=2')) return ok({ events: [event('a')] });
      if (url.endsWith('seasontype=3')) return ok({ events: [event('p')] });
      throw new Error(`unexpected ${url}`);
    };

    const { events, failures } = await fetchSeasonSchedules(['1', '2'], 2024, mockContext);
    assert.deepStrictEqual(events.map((e) => e.id), ['a', 'b', 'p']);
    assert.deepStrictEqual(failures, []);
  });

  await t.test('collects failures instead of throwing', async () => {
    global.fetch = async (url) => {
      if (url.includes('/teams/2/')) return { ok: false, status: 500, text: async () => 'boom' };
      return ok({ events: [event('a')] });
    };

    const { events, failures } = await fetchSeasonSchedules(['1', '2'], 2024, mockContext, { seasonTypes: [2] });
    assert.deepStrictEqual(events.map((e) => e.id), ['a']);
    assert.strictEqual(failures.length, 1);
    assert.strictEqual(failures[0].teamId, '2');
    assert.strictEqual(failures[0].seasonType, 2);
  });
});

test('fetchGameSummaries', async (t) => {
  t.after(() => delete process.env.ESPN_FETCH_RETRIES);
  process.env.ESPN_FETCH_RETRIES = '1';

  await t.test('builds landings and collects failures', async () => {
    global.fetch = async (url) => {
      if (url.endsWith('event=bad')) return { ok: false, status: 404, text: async () => 'missing' };
      return ok({ header: { id: url.split('event=')[1] }, boxscore: { teams: [] } });
    };

    const { landings, failures } = await fetchGameSummaries(['g1', 'bad', 'g2'], mockContext);
    assert.deepStrictEqual(landings.map((l) => l.game_id), ['g1', 'g2']);
    assert.deepStrictEqual(failures.map((f) => f.gameId), ['bad']);
  });
});

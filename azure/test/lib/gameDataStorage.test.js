const test = require('node:test');
const assert = require('node:assert');
const { buildBlobName } = require('../../src/lib/gameDataStorage');

test('buildBlobName', async (t) => {
  await t.test('constructs correct blob path', () => {
    const timestamp = new Date('2026-09-30T18:45:23.123Z');
    const result = buildBlobName('teams', timestamp);

    assert.strictEqual(result, 'teams/2026-09-30/2026-09-30T18:45:23.123Z.json');
  });

  await t.test('works with all dataset prefixes', () => {
    const timestamp = new Date('2026-01-15T12:00:00.000Z');
    const prefixes = ['teams', 'players', 'standings', 'rosters', 'schedules', 'games', 'game-team-stats', 'injury'];

    for (const prefix of prefixes) {
      const result = buildBlobName(prefix, timestamp);
      assert.ok(result.startsWith(`${prefix}/`));
      assert.ok(result.endsWith('.json'));
      assert.ok(result.includes('2026-01-15'));
    }
  });

  await t.test('preserves ISO timestamp precision', () => {
    const timestamp = new Date('2026-06-15T14:23:45.789Z');
    const result = buildBlobName('test', timestamp);

    assert.ok(result.includes('2026-06-15T14:23:45.789Z'));
  });
});

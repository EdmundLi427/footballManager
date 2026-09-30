const test = require('node:test');
const assert = require('node:assert');
const {
  dedupeSchedulesByGameId,
  pluckGameHeader,
  pluckBoxscoreTeamStats,
  selectTargetGames,
} = require('../../src/lib/espnAssemble');

test('dedupeSchedulesByGameId', async (t) => {
  await t.test('deduplicates games that appear in multiple team schedules', () => {
    const schedule1 = [
      { id: 'game_1', week: 1, date: '2026-09-06' },
      { id: 'game_2', week: 1, date: '2026-09-07' },
    ];

    const schedule2 = [
      { id: 'game_2', week: 1, date: '2026-09-07' }, // Duplicate
      { id: 'game_3', week: 1, date: '2026-09-08' },
    ];

    const result = dedupeSchedulesByGameId([schedule1, schedule2]);
    assert.strictEqual(result.length, 3);
    assert.deepStrictEqual(
      result.map((g) => g.id),
      ['game_1', 'game_2', 'game_3']
    );
  });

  await t.test('preserves first occurrence', () => {
    const schedules = [
      [{ id: 'game_1', data: 'first', week: 1 }],
      [{ id: 'game_1', data: 'second', week: 1 }],
    ];

    const result = dedupeSchedulesByGameId(schedules);
    assert.strictEqual(result[0].data, 'first');
  });
});

test('pluckGameHeader', async (t) => {
  await t.test('extracts game header info', () => {
    const summaryData = {
      header: {
        uid: 'game_401547439',
        competitions: [
          {
            date: '2026-09-13T20:20Z',
            status: { type: 'Final' },
            venue: { fullName: 'Arrowhead Stadium' },
          },
        ],
      },
      gameInfo: {
        attendance: 76414,
        venue: {
          fullName: 'Arrowhead Stadium',
          address: { city: 'Kansas City' },
        },
      },
    };

    const result = pluckGameHeader(summaryData);
    assert.strictEqual(result.id, 'game_401547439');
    assert.strictEqual(result.date, '2026-09-13T20:20Z');
    assert.strictEqual(result.status, 'Final');
    assert.strictEqual(result.completed, true);
    assert.strictEqual(result.venue, 'Arrowhead Stadium');
    assert.strictEqual(result.city, 'Kansas City');
    assert.strictEqual(result.attendance, 76414);
  });

  await t.test('handles missing fields gracefully', () => {
    const summaryData = { header: {}, gameInfo: {} };
    const result = pluckGameHeader(summaryData);
    assert.strictEqual(result.id, undefined);
    assert.strictEqual(result.completed, false);
  });
});

test('pluckBoxscoreTeamStats', async (t) => {
  await t.test('extracts team stats from boxscore', () => {
    const summaryData = {
      boxscore: {
        teams: [
          {
            team: { id: '12', displayName: 'Kansas City Chiefs', abbreviation: 'KC' },
            statistics: [
              { name: 'firstDowns', value: 28, displayValue: '28' },
              { name: 'totalYards', value: 412, displayValue: '412' },
            ],
          },
          {
            team: { id: '25', displayName: 'Detroit Lions', abbreviation: 'DET' },
            statistics: [{ name: 'firstDowns', value: 22, displayValue: '22' }],
          },
        ],
      },
    };

    const result = pluckBoxscoreTeamStats(summaryData);
    assert.strictEqual(result.length, 2);

    // Check home team (Chiefs)
    assert.strictEqual(result[0].teamId, '12');
    assert.strictEqual(result[0].teamName, 'Kansas City Chiefs');
    assert.strictEqual(result[0].firstDowns, 28);
    assert.strictEqual(result[0].firstDowns_display, '28');
    assert.strictEqual(result[0].totalYards, 412);

    // Check away team (Lions)
    assert.strictEqual(result[1].teamId, '25');
    assert.strictEqual(result[1].firstDowns, 22);
  });

  await t.test('handles empty statistics', () => {
    const summaryData = {
      boxscore: {
        teams: [{ team: { id: '1', displayName: 'Team A', abbreviation: 'A' }, statistics: [] }],
      },
    };

    const result = pluckBoxscoreTeamStats(summaryData);
    assert.strictEqual(result.length, 1);
    assert.strictEqual(result[0].teamId, '1');
  });
});

test('selectTargetGames', async (t) => {
  await t.test('selects games that are not completed', () => {
    const schedules = [
      {
        id: 'game_1',
        date: '2026-10-01T20:00Z',
        competitions: [{ status: { type: 'Scheduled' } }],
      },
      {
        id: 'game_2',
        date: '2026-10-02T20:00Z',
        competitions: [{ status: { type: 'InProgress' } }],
      },
    ];

    const result = selectTargetGames(schedules);
    assert.deepStrictEqual(result, ['game_1', 'game_2']);
  });

  await t.test('includes recently completed games (within trailing window)', () => {
    const now = new Date('2026-10-05T00:00Z');
    const schedules = [
      {
        id: 'game_1',
        date: '2026-10-04T20:00Z', // 1 day ago - within 3-day window
        competitions: [{ status: { type: 'Final' } }],
      },
      {
        id: 'game_2',
        date: '2026-09-27T20:00Z', // 8 days ago - outside 3-day window
        competitions: [{ status: { type: 'Final' } }],
      },
    ];

    const result = selectTargetGames(schedules, now);
    assert.deepStrictEqual(result, ['game_1']);
  });

  await t.test('respects max targets ceiling', () => {
    process.env.GAME_SUMMARY_MAX_TARGETS = '2';
    const schedules = Array.from({ length: 10 }, (_, i) => ({
      id: `game_${i}`,
      date: '2026-10-01T20:00Z',
      competitions: [{ status: { type: 'Scheduled' } }],
    }));

    const result = selectTargetGames(schedules);
    assert.strictEqual(result.length, 2);
    delete process.env.GAME_SUMMARY_MAX_TARGETS;
  });
});

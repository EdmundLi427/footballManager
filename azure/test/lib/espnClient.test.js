const test = require('node:test');
const assert = require('node:assert');
const { getCurrentSeasonYear, fetchWithRetry, mapWithConcurrency } = require('../../src/lib/espnClient');

test('getCurrentSeasonYear', async (t) => {
  await t.test('returns prior year for Jan/Feb', () => {
    const jan = new Date('2026-01-15');
    assert.strictEqual(getCurrentSeasonYear(jan), 2025);

    const feb = new Date('2026-02-28');
    assert.strictEqual(getCurrentSeasonYear(feb), 2025);
  });

  await t.test('returns current year for Mar-Dec', () => {
    const march = new Date('2026-03-01');
    assert.strictEqual(getCurrentSeasonYear(march), 2026);

    const sept = new Date('2026-09-30');
    assert.strictEqual(getCurrentSeasonYear(sept), 2026);

    const dec = new Date('2026-12-31');
    assert.strictEqual(getCurrentSeasonYear(dec), 2026);
  });
});

test('fetchWithRetry', async (t) => {
  const mockContext = { log: () => {}, error: () => {} };

  await t.test('returns response on first success', async () => {
    let callCount = 0;
    const mockFetch = async () => {
      callCount++;
      return { ok: true, json: async () => ({ data: 'test' }) };
    };

    global.fetch = mockFetch;
    const response = await fetchWithRetry('http://example.com', {}, mockContext, 3);
    assert.strictEqual(callCount, 1);
    assert.strictEqual(response.ok, true);
  });

  await t.test('retries on non-ok response', async () => {
    let callCount = 0;
    const mockFetch = async () => {
      callCount++;
      if (callCount < 3) {
        return { ok: false, status: 503, text: async () => 'Service Unavailable' };
      }
      return { ok: true, json: async () => ({ data: 'success' }) };
    };

    global.fetch = mockFetch;
    const response = await fetchWithRetry('http://example.com', {}, mockContext, 3);
    assert.strictEqual(callCount, 3);
    assert.strictEqual(response.ok, true);
  });

  await t.test('throws after exhausting retries', async () => {
    const mockFetch = async () => {
      return { ok: false, status: 500, text: async () => 'Server Error' };
    };

    global.fetch = mockFetch;
    await assert.rejects(
      () => fetchWithRetry('http://example.com', {}, mockContext, 2),
      /ESPN fetch failed after 2 retries/
    );
  });
});

test('mapWithConcurrency', async (t) => {
  await t.test('maintains result order', async () => {
    const items = [1, 2, 3, 4, 5];
    const results = await mapWithConcurrency(items, 2, async (item) => item * 2);
    assert.deepStrictEqual(results, [2, 4, 6, 8, 10]);
  });

  await t.test('respects concurrency limit', async () => {
    const items = Array.from({ length: 10 }, (_, i) => i);
    let maxConcurrent = 0;
    let currentConcurrent = 0;

    const results = await mapWithConcurrency(items, 3, async (item) => {
      currentConcurrent++;
      maxConcurrent = Math.max(maxConcurrent, currentConcurrent);
      await new Promise((resolve) => setTimeout(resolve, 1));
      currentConcurrent--;
      return item * 2;
    });

    assert.ok(maxConcurrent <= 3, `Expected max concurrent <= 3, got ${maxConcurrent}`);
    assert.strictEqual(results.length, 10);
  });

  await t.test('catches errors per item', async () => {
    const items = [1, 2, 3, 4];
    const results = await mapWithConcurrency(items, 2, async (item) => {
      if (item === 2) throw new Error('Item 2 failed');
      return item * 10;
    });

    assert.strictEqual(results[0], 10);
    assert.ok(results[1]._error instanceof Error);
    assert.strictEqual(results[2], 30);
    assert.strictEqual(results[3], 40);
  });
});

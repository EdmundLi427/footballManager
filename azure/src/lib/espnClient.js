const ESPN_SITES = {
  SITE_BASE: 'https://site.api.espn.com/apis/site/v2/sports/football/nfl',
  CORE_BASE: 'https://sports.core.api.espn.com/v3/sports/football/nfl',
  STANDINGS_URL: 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
};

/**
 * Resolves the current NFL season year.
 * Jan/Feb belong to the previous season (playoffs for prior year).
 * Mar-Dec belong to the current calendar year.
 */
function getCurrentSeasonYear(date = new Date()) {
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth(); // 0 = Jan, 11 = Dec
  return month <= 1 ? year - 1 : year;
}

/**
 * Fetches a URL with retry logic and exponential backoff.
 * @param {string} url - The URL to fetch
 * @param {object} options - Fetch options (headers, params, etc.)
 * @param {object} context - Azure Functions context for logging
 * @param {number} maxRetries - Maximum retry attempts (default from env)
 * @returns {Promise<Response>}
 */
async function fetchWithRetry(url, options = {}, context, maxRetries = 3) {
  const retries = parseInt(process.env.ESPN_FETCH_RETRIES || String(maxRetries), 10);

  for (let attempt = 0; attempt < retries; attempt++) {
    try {
      const response = await fetch(url, options);

      if (response.ok) {
        return response;
      }

      // Non-200 response; log but allow retry logic to decide
      const body = await response.text();
      const errorMsg = `ESPN ${response.status} ${url}: ${body.slice(0, 200)}`;

      if (attempt < retries - 1) {
        context.log(`[FETCH_RETRY] Attempt ${attempt + 1}/${retries} failed: ${errorMsg}`);
      } else {
        context.error(`[FETCH_FAILED] Exhausted retries: ${errorMsg}`);
      }
    } catch (error) {
      const errorMsg = error.message;

      if (attempt < retries - 1) {
        context.log(`[FETCH_RETRY] Attempt ${attempt + 1}/${retries} failed: ${errorMsg}`);
      } else {
        context.error(`[FETCH_FAILED] Exhausted retries: ${errorMsg}`);
      }
    }

    if (attempt < retries - 1) {
      const delayMs = 1000 * (attempt + 1);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }

  throw new Error(`ESPN fetch failed after ${retries} retries: ${url}`);
}

/**
 * Executes async functions with bounded concurrency.
 * Maintains order of results.
 * @param {any[]} items - Items to process
 * @param {number} concurrencyLimit - Max concurrent operations
 * @param {Function} fn - Async function(item, index) -> Promise<result>
 * @returns {Promise<any[]>} Results in original order
 */
async function mapWithConcurrency(items, concurrencyLimit, fn) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function worker() {
    while (nextIndex < items.length) {
      const index = nextIndex++;
      try {
        results[index] = await fn(items[index], index);
      } catch (error) {
        results[index] = { _error: error };
      }
    }
  }

  // Create workers
  const limit = Math.min(concurrencyLimit, items.length);
  const workers = Array.from({ length: limit }, () => worker());

  // Wait for all to complete
  await Promise.all(workers);

  return results;
}

/**
 * Paginates through an ESPN API endpoint that returns pageCount/limit structure.
 * @param {string} baseUrl - The API endpoint
 * @param {object} context - Azure Functions context
 * @param {object} initialResponse - The first response (already fetched)
 * @returns {Promise<any[]>} Flattened array of all items across pages
 */
async function paginateEspnEndpoint(baseUrl, context, initialResponse, limit = 1000) {
  const allItems = (initialResponse.items || []).slice();
  const pageCount = initialResponse.pageCount || 1;

  for (let page = 2; page <= pageCount; page++) {
    const url = `${baseUrl}&limit=${limit}&page=${page}`;
    const response = await fetchWithRetry(url, {}, context);
    const data = await response.json();
    allItems.push(...(data.items || []));
  }

  return allItems;
}

module.exports = {
  ESPN_SITES,
  getCurrentSeasonYear,
  fetchWithRetry,
  mapWithConcurrency,
  paginateEspnEndpoint,
};

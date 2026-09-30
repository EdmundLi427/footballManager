const { app } = require('@azure/functions');
const { BlobServiceClient } = require('@azure/storage-blob');

const blobService = BlobServiceClient.fromConnectionString(
  process.env.NEWS_STORAGE_CONNECTION || process.env.AzureWebJobsStorage
);
const container = blobService.getContainerClient(process.env.NEWS_CONTAINER || 'nfl-news');

app.timer('fetchGmaeNews', {
  schedule: '0 0 0 * * *',
  handler: async (timer, context) => {
    const startTime = Date.now();
    const runTimestamp = new Date().toISOString();
    const summary = { timestamp: runTimestamp, status: 'pending', articlesCount: 0, uploadedBytes: 0, blobName: null, durationMs: 0 };

    try {
      context.log('[FETCH_START] Fetching NFL injury news from FantasyPros API');
      const url = new URL('https://api.fantasypros.com/public/v2/json/nfl/news');
      url.search = new URLSearchParams({ category: 'injury', pageIndex: '1' });
      context.log(`[API_CALL] GET ${url.toString()}`);

      const resp = await fetch(url, { headers: { 'x-api-key': process.env.FANTASY_PRO_API_KEY } });
      context.log(`[API_RESPONSE] Status: ${resp.status}, Content-Length: ${resp.headers.get('content-length') || 'unknown'}`);

      if (!resp.ok) {
        const errorBody = await resp.text();
        throw new Error(`FantasyPros ${resp.status}: ${errorBody}`);
      }

      const data = await resp.json();
      summary.articlesCount = data.items ? data.items.length : 0;
      context.log(`[DATA_PARSED] Articles received: ${summary.articlesCount} (total available: ${data.count})`);

      await container.createIfNotExists();
      const now = new Date().toISOString();
      const blobName = `injury/${now.slice(0, 10)}/${now}.json`;
      const body = JSON.stringify(data);
      const bodyBytes = Buffer.byteLength(body);
      summary.uploadedBytes = bodyBytes;
      summary.blobName = blobName;

      context.log(`[UPLOAD_START] Uploading to blob: ${blobName} (${bodyBytes} bytes)`);
      const blobClient = container.getBlockBlobClient(blobName);
      await blobClient.upload(body, bodyBytes, {
        blobHTTPHeaders: { blobContentType: 'application/json' },
      });

      summary.status = 'success';
      summary.durationMs = Date.now() - startTime;
      context.log(`[UPLOAD_SUCCESS] Blob saved: ${blobClient.url}`);
      context.log(`[STORAGE_INFO] Account: ${blobService.accountName}, Container: ${container.containerName}`);
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
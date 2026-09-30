const { BlobServiceClient } = require('@azure/storage-blob');

/**
 * Initializes and caches the Azure Blob Storage client and container.
 * Shared across all invocations for efficiency.
 */
let blobService;
let containerClient;

function initializeStorageClient() {
  if (containerClient) {
    return containerClient;
  }

  const connectionString =
    process.env.NEWS_STORAGE_CONNECTION || process.env.AzureWebJobsStorage;
  const containerName = process.env.ALSOURCE_CONTAINER || 'alsource';

  blobService = BlobServiceClient.fromConnectionString(connectionString);
  containerClient = blobService.getContainerClient(containerName);

  return containerClient;
}

/**
 * Constructs a blob path following the standard pattern.
 * Format: {prefix}/{YYYY-MM-DD}/{ISO-timestamp}.json
 *
 * @param {string} prefix - Dataset prefix (e.g. 'teams', 'schedules', 'games')
 * @param {Date} timestamp - The timestamp for this run
 * @returns {string} Full blob path
 */
function buildBlobName(prefix, timestamp) {
  const isoString = timestamp.toISOString();
  const dateString = isoString.slice(0, 10); // YYYY-MM-DD
  return `${prefix}/${dateString}/${isoString}.json`;
}

/**
 * Uploads JSON data to blob storage.
 * Creates container if needed, uploads blob with proper content type.
 *
 * @param {string} prefix - Dataset prefix (e.g. 'teams', 'schedules')
 * @param {object} data - Data to serialize and upload
 * @param {object} context - Azure Functions context for logging
 * @returns {Promise<{blobName: string, bytes: number}>} Metadata about the uploaded blob
 */
async function uploadJsonBlob(prefix, data, context) {
  const container = initializeStorageClient();

  // Ensure container exists
  await container.createIfNotExists();

  // Serialize and upload
  const timestamp = new Date();
  const blobName = buildBlobName(prefix, timestamp);
  const body = JSON.stringify(data);
  const bodyBytes = Buffer.byteLength(body);

  context.log(`[UPLOAD_START] ${prefix} blob: ${blobName} (${bodyBytes} bytes)`);

  const blobClient = container.getBlockBlobClient(blobName);
  await blobClient.upload(body, bodyBytes, {
    blobHTTPHeaders: { blobContentType: 'application/json' },
  });

  context.log(`[UPLOAD_SUCCESS] ${prefix} blob: ${blobClient.url}`);

  return {
    blobName,
    bytes: bodyBytes,
  };
}

/**
 * Returns info about the storage account and container (for logging).
 * @returns {object} {accountName, containerName}
 */
function getStorageInfo() {
  const container = initializeStorageClient();
  return {
    accountName: blobService?.accountName || 'unknown',
    containerName: container?.containerName || 'unknown',
  };
}

module.exports = {
  buildBlobName,
  uploadJsonBlob,
  getStorageInfo,
};

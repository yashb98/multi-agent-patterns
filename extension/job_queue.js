/**
 * Job Queue Module - IndexedDB CRUD for job automation
 * Manages persistent storage of jobs, patterns, checkpoints, and metrics
 * Database: jobpulse_db v1
 */

const DB_NAME = 'jobpulse_db';
const DB_VERSION = 1;

// Object store configurations
const STORES = {
  job_queue: {
    keyPath: 'id',
    autoIncrement: true,
    indexes: [
      { name: 'url', keyPath: 'url', unique: true },
      { name: 'platform', keyPath: 'platform', unique: false },
      { name: 'apply_status', keyPath: 'apply_status', unique: false },
      { name: 'scraped_at', keyPath: 'scraped_at', unique: false },
    ],
  },
  ralph_patterns: {
    keyPath: 'id',
    autoIncrement: true,
    indexes: [
      { name: 'platform', keyPath: 'platform', unique: false },
      { name: 'fix_type', keyPath: 'fix_type', unique: false },
    ],
  },
  scan_checkpoints: {
    keyPath: 'platform',
    autoIncrement: false,
  },
  psi_history: {
    keyPath: 'id',
    autoIncrement: true,
    indexes: [
      { name: 'platform', keyPath: 'platform', unique: false },
      { name: 'week', keyPath: 'week', unique: false },
    ],
  },
};

let dbInstance = null;

/**
 * Initialize IndexedDB and create object stores if needed
 * @returns {Promise<IDBDatabase>} Open database instance
 */
export async function initDB() {
  return new Promise((resolve, reject) => {
    // Return cached instance if already open
    if (dbInstance) {
      resolve(dbInstance);
      return;
    }

    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => {
      console.error('IndexedDB open failed:', request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      dbInstance = request.result;
      resolve(dbInstance);
    };

    request.onupgradeneeded = (event) => {
      const db = event.target.result;

      // Create object stores
      Object.entries(STORES).forEach(([storeName, config]) => {
        if (!db.objectStoreNames.contains(storeName)) {
          const store = db.createObjectStore(storeName, {
            keyPath: config.keyPath,
            autoIncrement: config.autoIncrement,
          });

          // Create indexes
          if (config.indexes) {
            config.indexes.forEach((indexConfig) => {
              store.createIndex(
                indexConfig.name,
                indexConfig.keyPath,
                { unique: indexConfig.unique || false }
              );
            });
          }
        }
      });

      console.log('IndexedDB initialized:', DB_NAME);
    };
  });
}

/**
 * Add a new job to the queue, deduplicating by URL
 * @param {Object} job - Job object with url, title, company, platform, etc.
 * @returns {Promise<number>} Job ID (auto-incremented)
 */
export async function addJob(job) {
  const db = await initDB();

  // Check for duplicate URL
  const exists = await getJobByUrl(job.url);
  if (exists) {
    console.warn(`Job with URL already exists: ${job.url}`);
    return exists.id;
  }

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readwrite');
    const store = transaction.objectStore('job_queue');

    // Ensure required fields
    const jobData = {
      scraped_at: job.scraped_at || Date.now(),
      apply_status: job.apply_status || 'pending',
      ...job,
    };

    const request = store.add(jobData);

    request.onerror = () => {
      console.error('Failed to add job:', request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      const jobId = request.result;
      console.log(`Job added with ID: ${jobId}`);
      resolve(jobId);
    };
  });
}

/**
 * Get a single job by ID
 * @param {number} id - Job ID
 * @returns {Promise<Object|null>} Job object or null if not found
 */
export async function getJob(id) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const request = store.get(id);

    request.onerror = () => {
      console.error(`Failed to get job ${id}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result || null);
    };
  });
}

/**
 * Get a job by URL (unique index lookup)
 * @param {string} url - Job URL
 * @returns {Promise<Object|null>} Job object or null if not found
 */
export async function getJobByUrl(url) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const index = store.index('url');
    const request = index.get(url);

    request.onerror = () => {
      console.error(`Failed to get job by URL ${url}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result || null);
    };
  });
}

/**
 * Update a job with partial updates
 * @param {number} id - Job ID
 * @param {Object} updates - Fields to update
 * @returns {Promise<void>}
 */
export async function updateJob(id, updates) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readwrite');
    const store = transaction.objectStore('job_queue');
    const getRequest = store.get(id);

    getRequest.onerror = () => {
      console.error(`Failed to update job ${id}:`, getRequest.error);
      reject(getRequest.error);
    };

    getRequest.onsuccess = () => {
      const job = getRequest.result;
      if (!job) {
        reject(new Error(`Job ${id} not found`));
        return;
      }

      const updatedJob = { ...job, ...updates };
      const putRequest = store.put(updatedJob);

      putRequest.onerror = () => {
        console.error(`Failed to save updated job ${id}:`, putRequest.error);
        reject(putRequest.error);
      };

      putRequest.onsuccess = () => {
        console.log(`Job ${id} updated`);
        resolve();
      };
    };
  });
}

/**
 * Get all jobs with a specific apply_status
 * @param {string} status - apply_status value (pending, ready, applied, etc.)
 * @returns {Promise<Array>} Array of matching jobs
 */
export async function getJobsByStatus(status) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const index = store.index('apply_status');
    const request = index.getAll(status);

    request.onerror = () => {
      console.error(`Failed to get jobs with status ${status}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result || []);
    };
  });
}

/**
 * Get all jobs for a specific platform
 * @param {string} platform - Platform name (linkedin, indeed, greenhouse, etc.)
 * @returns {Promise<Array>} Array of matching jobs
 */
export async function getJobsByPlatform(platform) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const index = store.index('platform');
    const request = index.getAll(platform);

    request.onerror = () => {
      console.error(`Failed to get jobs for platform ${platform}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result || []);
    };
  });
}

/**
 * Get all pending jobs (apply_status "pending" or "ready")
 * @returns {Promise<Array>} Array of pending jobs
 */
export async function getPendingJobs() {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const allRequest = store.getAll();

    allRequest.onerror = () => {
      console.error('Failed to get pending jobs:', allRequest.error);
      reject(allRequest.error);
    };

    allRequest.onsuccess = () => {
      const jobs = allRequest.result || [];
      const pending = jobs.filter(
        (job) => job.apply_status === 'pending' || job.apply_status === 'ready'
      );
      resolve(pending);
    };
  });
}

/**
 * Get all approved jobs (apply_status "approved")
 * @returns {Promise<Array>} Array of approved jobs
 */
export async function getApprovedJobs() {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const allRequest = store.getAll();

    allRequest.onerror = () => {
      console.error('Failed to get approved jobs:', allRequest.error);
      reject(allRequest.error);
    };

    allRequest.onsuccess = () => {
      const jobs = allRequest.result || [];
      const approved = jobs.filter((job) => job.apply_status === 'approved');
      resolve(approved);
    };
  });
}

/**
 * Mark a job as applied
 * @param {number} id - Job ID
 * @returns {Promise<void>}
 */
export async function markApplied(id) {
  return updateJob(id, {
    apply_status: 'applied',
    applied_at: Date.now(),
  });
}

/**
 * Mark a job as error and store error message
 * @param {number} id - Job ID
 * @param {string|Error} error - Error message or Error object
 * @returns {Promise<void>}
 */
export async function markError(id, error) {
  const errorMessage = error instanceof Error ? error.message : String(error);
  return updateJob(id, {
    apply_status: 'error',
    error: errorMessage,
    error_at: Date.now(),
  });
}

/**
 * Delete jobs older than specified days
 * @param {number} daysOld - Number of days in the past
 * @returns {Promise<number>} Count of deleted jobs
 */
export async function cleanupOldJobs(daysOld) {
  const db = await initDB();
  const cutoffTime = Date.now() - daysOld * 24 * 60 * 60 * 1000;

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readwrite');
    const store = transaction.objectStore('job_queue');
    const allRequest = store.getAll();

    allRequest.onerror = () => {
      console.error('Failed to cleanup old jobs:', allRequest.error);
      reject(allRequest.error);
    };

    allRequest.onsuccess = () => {
      const jobs = allRequest.result || [];
      let deletedCount = 0;

      jobs.forEach((job) => {
        if (job.scraped_at < cutoffTime) {
          const deleteRequest = store.delete(job.id);
          deleteRequest.onerror = () => {
            console.error(`Failed to delete job ${job.id}:`, deleteRequest.error);
          };
          deleteRequest.onsuccess = () => {
            deletedCount++;
          };
        }
      });

      transaction.oncomplete = () => {
        console.log(`Cleaned up ${deletedCount} jobs older than ${daysOld} days`);
        resolve(deletedCount);
      };

      transaction.onerror = () => {
        console.error('Cleanup transaction failed:', transaction.error);
        reject(transaction.error);
      };
    };
  });
}

/**
 * Get total count of jobs in queue
 * @returns {Promise<number>} Total job count
 */
export async function getJobCount() {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const request = store.count();

    request.onerror = () => {
      console.error('Failed to get job count:', request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result);
    };
  });
}

/**
 * Get statistics on jobs: counts by status and platform
 * @returns {Promise<Object>} Stats object { statusCounts, platformCounts, total }
 */
export async function getStats() {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['job_queue'], 'readonly');
    const store = transaction.objectStore('job_queue');
    const allRequest = store.getAll();

    allRequest.onerror = () => {
      console.error('Failed to get stats:', allRequest.error);
      reject(allRequest.error);
    };

    allRequest.onsuccess = () => {
      const jobs = allRequest.result || [];
      const statusCounts = {};
      const platformCounts = {};

      jobs.forEach((job) => {
        // Count by status
        statusCounts[job.apply_status] = (statusCounts[job.apply_status] || 0) + 1;
        // Count by platform
        platformCounts[job.platform] = (platformCounts[job.platform] || 0) + 1;
      });

      resolve({
        total: jobs.length,
        statusCounts,
        platformCounts,
      });
    };
  });
}

/**
 * Save a scan checkpoint for resume capability
 * @param {string} platform - Platform name
 * @param {Object} checkpoint - Checkpoint data { lastUrl, lastPage, timestamp, state }
 * @returns {Promise<void>}
 */
export async function saveScanCheckpoint(platform, checkpoint) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['scan_checkpoints'], 'readwrite');
    const store = transaction.objectStore('scan_checkpoints');

    const data = {
      platform,
      ...checkpoint,
      updated_at: Date.now(),
    };

    const request = store.put(data);

    request.onerror = () => {
      console.error(`Failed to save checkpoint for ${platform}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      console.log(`Checkpoint saved for ${platform}`);
      resolve();
    };
  });
}

/**
 * Get a scan checkpoint for a platform
 * @param {string} platform - Platform name
 * @returns {Promise<Object|null>} Checkpoint data or null if not found
 */
export async function getScanCheckpoint(platform) {
  const db = await initDB();

  return new Promise((resolve, reject) => {
    const transaction = db.transaction(['scan_checkpoints'], 'readonly');
    const store = transaction.objectStore('scan_checkpoints');
    const request = store.get(platform);

    request.onerror = () => {
      console.error(`Failed to get checkpoint for ${platform}:`, request.error);
      reject(request.error);
    };

    request.onsuccess = () => {
      resolve(request.result || null);
    };
  });
}

/**
 * Check if a job URL already exists in the queue
 * @param {string} url - Job URL
 * @returns {Promise<boolean>} True if URL exists, false otherwise
 */
export async function isDuplicate(url) {
  try {
    const job = await getJobByUrl(url);
    return !!job;
  } catch (error) {
    console.error(`Error checking duplicate for ${url}:`, error);
    return false;
  }
}

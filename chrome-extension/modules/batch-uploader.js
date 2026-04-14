export class BatchUploader {
  constructor() {
    this.pendingQueue = [];
    this.batchSize = 5;
    this.batchInterval = 5000;
    this.uploadTimer = null;
    this.apiEndpoint = this.normalizeEndpoint('http://localhost:3000/api/save-files');
    this.performAnalysisOnUpload = false;
    this.isUploading = false;
    this.isFlushing = false;
    this.stats = {
      uploadedFiles: 0,
      failedBatches: 0,
      lastError: null,
      lastUploadAt: null
    };
  }

  async enqueue(fileObject) {
    this.pendingQueue.push(fileObject);

    if (!this.uploadTimer) {
      this.uploadTimer = setTimeout(() => {
        this.processBatch();
      }, this.batchInterval);
    }

    if (this.pendingQueue.length >= this.batchSize) {
      this.processBatch();
    }
  }

  async queue(fileObject) {
    // Backward-compatible alias.
    return this.enqueue(fileObject);
  }

  async processBatch() {
    if (this.isUploading || this.pendingQueue.length === 0) {
      return;
    }

    this.isUploading = true;
    clearTimeout(this.uploadTimer);
    this.uploadTimer = null;

    const batch = this.pendingQueue.splice(0, this.batchSize);

    try {
      await this.upload(batch);
      console.log(`Successfully uploaded batch of ${batch.length} files`);
      this.stats.uploadedFiles += batch.length;
      this.stats.lastError = null;
      this.stats.lastUploadAt = new Date().toISOString();
    } catch (error) {
      console.error('Batch upload failed:', error);
      this.pendingQueue.unshift(...batch);
      this.stats.failedBatches += 1;
      this.stats.lastError = error.message;
      
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icons/icon48.png',
        title: 'Upload Failed',
        message: `Failed to upload ${batch.length} files. Will retry.`
      });
    } finally {
      this.isUploading = false;
      
      if (this.pendingQueue.length > 0 && !this.isFlushing) {
        this.uploadTimer = setTimeout(() => {
          this.processBatch();
        }, this.batchInterval);
      }
    }
  }

  async flushAll() {
    this.isFlushing = true;
    clearTimeout(this.uploadTimer);
    this.uploadTimer = null;

    while (this.isUploading) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }

    while (this.pendingQueue.length > 0) {
      await this.processBatch();
      while (this.isUploading) {
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (this.stats.lastError) {
        break;
      }
    }

    this.isFlushing = false;

    if (this.pendingQueue.length > 0) {
      this.uploadTimer = setTimeout(() => {
        this.processBatch();
      }, this.batchInterval);
    }
  }

  async upload(files) {
    if (!this.apiEndpoint) {
      throw new Error('API endpoint is not configured');
    }

    const payload = {
      metadata: {
        uploadDate: new Date().toISOString(),
        batchSize: files.length,
        version: '3.0.0',
        performAnalysis: this.performAnalysisOnUpload
      },
      files: files
    };

    const response = await fetch(this.apiEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    return await response.json();
  }

  setEndpoint(url) {
    this.apiEndpoint = this.normalizeEndpoint(url);
  }

  setPerformAnalysisOnUpload(enabled) {
    this.performAnalysisOnUpload = enabled === true;
  }

  normalizeEndpoint(url) {
    if (!url || typeof url !== 'string') {
      return 'http://localhost:3000/api/save-files';
    }

    try {
      const parsed = new URL(url.trim());
      parsed.pathname = '/api/save-files';
      parsed.search = '';
      parsed.hash = '';
      return parsed.toString();
    } catch (error) {
      return 'http://localhost:3000/api/save-files';
    }
  }

  getStats() {
    return {
      ...this.stats,
      pendingQueueLength: this.pendingQueue.length,
      endpoint: this.apiEndpoint,
      isUploading: this.isUploading
    };
  }
}

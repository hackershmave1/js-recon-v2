document.addEventListener('DOMContentLoaded', async () => {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const exportBtn = document.getElementById('exportBtn');
  const clearBtn = document.getElementById('clearBtn');
  const settingsBtn = document.getElementById('settingsBtn');
  const statusIndicator = document.getElementById('statusIndicator');
  const statusText = document.getElementById('statusText');
  const diagText = document.getElementById('diagText');
  const fileCount = document.getElementById('fileCount');
  const queueCount = document.getElementById('queueCount');
  const fileList = document.getElementById('fileList');
  const performAnalysisOnUploadToggle = document.getElementById('performAnalysisOnUploadToggle');

  function getObjectUrlFactory() {
    if (window.URL && typeof window.URL.createObjectURL === 'function') {
      return window.URL;
    }
    if (window.webkitURL && typeof window.webkitURL.createObjectURL === 'function') {
      return window.webkitURL;
    }
    return null;
  }

  function triggerJsonDownload(exportData, filename) {
    const objectUrlFactory = getObjectUrlFactory();
    if (!objectUrlFactory) {
      throw new Error('Object URL API is unavailable in popup context.');
    }

    const json = JSON.stringify(exportData, null, 2);
    const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
    const objectUrl = objectUrlFactory.createObjectURL(blob);

    try {
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      setTimeout(() => {
        objectUrlFactory.revokeObjectURL(objectUrl);
      }, 30_000);
    }
  }

  async function updateStatus() {
    const response = await chrome.runtime.sendMessage({ action: 'getStatus' });
    const uploader = response.uploader || {};
    const processing = response.processingStats || {};
    
    if (response.isCapturing) {
      statusIndicator.classList.add('active');
      statusText.textContent = 'Capturing...';
      startBtn.style.display = 'none';
      stopBtn.style.display = 'block';
    } else {
      statusIndicator.classList.remove('active');
      if (uploader.lastError) {
        statusText.textContent = 'Stopped (upload error)';
      } else if (uploader.pendingQueueLength > 0) {
        statusText.textContent = 'Stopped (upload pending)';
      } else {
        statusText.textContent = 'Stopped';
      }
      startBtn.style.display = 'block';
      stopBtn.style.display = 'none';
    }
    
    fileCount.textContent = response.fileCount || 0;
    queueCount.textContent = response.queueLength || 0;
    queueCount.title = `Processing queue: ${response.queueLength || 0} | Upload queue: ${uploader.pendingQueueLength || 0}`;
    const processed = processing.processedFiles || 0;
    const failed = processing.failedFiles || 0;
    const reason = processing.lastFailureReason ? ` | Last: ${processing.lastFailureReason}` : '';
    diagText.textContent = `Processed: ${processed} | Failed: ${failed}${reason}`;
    diagText.title = processing.lastFailureMessage || '';
    if (performAnalysisOnUploadToggle) {
      performAnalysisOnUploadToggle.checked = response?.settings?.performAnalysisOnUpload === true;
    }
  }

  async function updateFileList() {
    const response = await chrome.runtime.sendMessage({ action: 'getFiles' });
    
    if (response.files && response.files.length > 0) {
      fileList.innerHTML = '';
      response.files.forEach((file) => {
        const item = document.createElement('div');
        item.className = 'file-item';

        const link = document.createElement('a');
        link.className = 'file-url';
        link.href = '#';
        link.textContent = file.url;
        link.addEventListener('click', (event) => {
          event.preventDefault();
          chrome.tabs.create({ url: file.url });
        });

        const meta = document.createElement('div');
        meta.className = 'file-meta';
        const bits = [
          `Size: ${(file.size / 1024).toFixed(2)} KB`,
          file.hasSourceMap ? 'Has source map' : null,
          !file.hasSourceMap && file.sourceMapFetchStatus && file.sourceMapFetchStatus !== 'not_detected' && file.sourceMapFetchStatus !== 'disabled'
            ? `Map: ${file.sourceMapFetchStatus}`
            : null,
          file.repPlusImportedHints > 0 ? `REP+ hints: ${file.repPlusImportedHints}` : null,
          file.isMinified ? 'Minified' : null
        ].filter(Boolean);
        meta.textContent = bits.join(' • ');

        item.appendChild(link);
        item.appendChild(meta);
        fileList.appendChild(item);
      });
    } else {
      fileList.innerHTML = '<div class="empty-state">No files captured yet</div>';
    }
  }

  startBtn.addEventListener('click', async () => {
    await chrome.runtime.sendMessage({ action: 'startCapture' });
    await updateStatus();
  });

  stopBtn.addEventListener('click', async () => {
    const result = await chrome.runtime.sendMessage({ action: 'stopCapture' });
    if (result?.uploader?.lastError) {
      alert(`Upload flush encountered an error: ${result.uploader.lastError}`);
    }
    await updateStatus();
    await updateFileList();
  });

  exportBtn.addEventListener('click', async () => {
    const originalLabel = exportBtn.textContent;
    exportBtn.disabled = true;
    exportBtn.textContent = 'Preparing export...';

    try {
      const result = await chrome.runtime.sendMessage({ action: 'getExportData' });
      if (!result || result.success === false) {
        throw new Error(result?.error || 'Failed to prepare export payload');
      }

      exportBtn.textContent = 'Downloading...';
      triggerJsonDownload(result.exportData, result.filename || 'js-extraction.json');
    } catch (error) {
      alert(`Export failed: ${error.message}`);
    } finally {
      exportBtn.disabled = false;
      exportBtn.textContent = originalLabel;
    }
  });

  clearBtn.addEventListener('click', async () => {
    if (confirm('Clear all captured files?')) {
      await chrome.runtime.sendMessage({ action: 'clearFiles' });
      await updateStatus();
      await updateFileList();
    }
  });

  settingsBtn.addEventListener('click', () => {
    chrome.runtime.openOptionsPage();
  });

  performAnalysisOnUploadToggle.addEventListener('change', async () => {
    await chrome.runtime.sendMessage({
      action: 'updateSettings',
      settings: {
        performAnalysisOnUpload: performAnalysisOnUploadToggle.checked
      }
    });
    await updateStatus();
  });

  await updateStatus();
  await updateFileList();

  setInterval(async () => {
    await updateStatus();
    await updateFileList();
  }, 2000);
});

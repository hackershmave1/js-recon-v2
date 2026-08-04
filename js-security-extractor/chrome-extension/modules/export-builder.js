export function toExportFile(file, includeContent = false) {
  if (includeContent) {
    return file;
  }

  return {
    url: file.url,
    contentHash: file.contentHash,
    sessionId: file.sessionId,
    tabId: file.tabId,
    frameId: file.frameId,
    capturedAt: file.capturedAt,
    requestTimestamp: file.requestTimestamp,
    statusCode: file.statusCode,
    method: file.method,
    headers: file.headers,
    contentType: file.contentType,
    contentEncoding: file.contentEncoding,
    contentLength: file.contentLength,
    isMinified: file.isMinified,
    hasSourceMap: file.hasSourceMap,
    sourceMapUrl: file.sourceMapUrl,
    sourceMapFetchStatus: file.sourceMapFetchStatus,
    sourceMapFetchError: file.sourceMapFetchError,
    dependencies: file.dependencies,
    initiator: file.initiator,
    documentUrl: file.documentUrl,
    needsServerProcessing: file.needsServerProcessing
  };
}

export function buildExportData({
  sessionId,
  files,
  includeContent = false,
  version = '3.0.0',
  exportDate = null
}) {
  const normalizedFiles = Array.isArray(files) ? files : [];
  const exportFiles = normalizedFiles.map((file) => toExportFile(file, includeContent));

  return {
    metadata: {
      sessionId: sessionId || 'unknown',
      exportDate: exportDate || new Date().toISOString(),
      totalFiles: normalizedFiles.length,
      version,
      includeContent
    },
    files: exportFiles
  };
}

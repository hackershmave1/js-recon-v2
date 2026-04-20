(function attachFailureUtils(globalObject) {
    function normalizeText(value) {
        return typeof value === 'string' ? value.trim() : '';
    }

    function sourceLabel(source) {
        if (source === 'sourcemap') return 'Source map';
        if (source === 'capture_fetch') return 'Capture/fetch';
        return 'Analysis';
    }

    function sourceGuidance(source) {
        if (source === 'sourcemap') {
            return 'Verify source map accessibility or disable source map processing, then retry.';
        }
        if (source === 'capture_fetch') {
            return 'Retry after confirming the URL is reachable and API/network access is healthy.';
        }
        return 'Retry analysis after checking extractor options and backend analyzer health.';
    }

    function inferFailureSource(message, sourceMapStatus, sourceMapError) {
        const status = normalizeText(sourceMapStatus).toLowerCase();
        const combined = `${normalizeText(message)} ${normalizeText(sourceMapError)}`.toLowerCase();

        if (
            status === 'failed' ||
            combined.includes('source map') ||
            combined.includes('sourcemap') ||
            combined.includes('.map')
        ) {
            return 'sourcemap';
        }

        if (
            combined.includes('fetch') ||
            combined.includes('network') ||
            combined.includes('download') ||
            combined.includes('http ') ||
            combined.includes('timeout')
        ) {
            return 'capture_fetch';
        }

        return 'analysis';
    }

    function deriveFileFailure(file) {
        const candidate = file || {};
        const analysisStatus = normalizeText(candidate.analysisStatus).toLowerCase();
        const analysisError = normalizeText(candidate.analysisError);
        const sourceMap = candidate.sourceMap || {};
        const sourceMapStatus = normalizeText(sourceMap.processingStatus).toLowerCase();
        const sourceMapError = normalizeText(sourceMap.processingError);

        if (analysisStatus !== 'failed') {
            return null;
        }

        const source = inferFailureSource(analysisError, sourceMapStatus, sourceMapError);
        const details = analysisError || sourceMapError || 'No detailed error message was stored.';
        return {
            source,
            label: sourceLabel(source),
            details,
            guidance: sourceGuidance(source)
        };
    }

    const api = {
        deriveFileFailure,
        inferFailureSource,
        sourceLabel,
        sourceGuidance
    };

    globalObject.DashboardFailureUtils = api;
})(typeof window !== 'undefined' ? window : globalThis);

/**
 * Rep+ Chrome Extension Bridge
 * 
 * Optional integration with rep+ Chrome extension for leveraging their
 * endpoint detection and Kingfisher rules-based secret detection.
 * 
 * This bridge allows your extension to:
 * 1. Detect if rep+ is installed
 * 2. Read rep+ analysis results from shared storage
 * 3. Merge rep+ results with your own analysis
 * 4. Avoid duplicate analysis where rep+ excels
 */

export class RepPlusBridge {
    constructor(extensionId = null) {
        this.repPlusExtensionId = typeof extensionId === 'string' ? extensionId.trim() : '';
        this.isRepPlusAvailable = false;
        this.lastSyncTime = 0;
        this.cache = new Map();
    }

    setExtensionId(extensionId) {
        this.repPlusExtensionId = typeof extensionId === 'string' ? extensionId.trim() : '';
        this.cache.clear();
        this.isRepPlusAvailable = false;
    }

    /**
     * Initialize bridge and detect rep+ installation
     */
    async initialize() {
        try {
            // Try to detect rep+ extension
            this.isRepPlusAvailable = await this.detectRepPlus();
            
            if (this.isRepPlusAvailable) {
                console.log('Rep+ bridge: rep+ extension detected');
                // Set up periodic sync if rep+ is available
                this.setupPeriodicSync();
            } else {
                console.log('Rep+ bridge: rep+ extension not found, running standalone');
            }
        } catch (error) {
            console.error('Rep+ bridge initialization failed:', error);
            this.isRepPlusAvailable = false;
        }
    }

    /**
     * Detect if rep+ extension is installed and accessible
     */
    async detectRepPlus() {
        try {
            // Method 1: Check if rep+ stores data in chrome.storage.local
            const repStorage = await chrome.storage.local.get(['rep_plus_version', 'rep_plus_active']);
            if (repStorage.rep_plus_version || repStorage.rep_plus_active) {
                return true;
            }

            // Method 2: Try to communicate directly with rep+ extension
            return await this.pingRepPlusExtension();
            
        } catch (error) {
            console.warn('Rep+ detection failed:', error);
            return false;
        }
    }

    /**
     * Attempt to ping rep+ extension directly
     */
    async pingRepPlusExtension() {
        if (!this.repPlusExtensionId) {
            return false;
        }
        return new Promise((resolve) => {
            try {
                chrome.runtime.sendMessage(this.repPlusExtensionId, 
                    { action: 'ping', source: 'js-security-extractor' },
                    (response) => {
                        if (chrome.runtime.lastError) {
                            resolve(false);
                        } else {
                            resolve(response && response.success === true);
                        }
                    }
                );
            } catch (error) {
                resolve(false);
            }
        });
    }

    /**
     * Get rep+ analysis results for current tab/session
     */
    async getRepPlusResults(tabId = null) {
        if (!this.isRepPlusAvailable) {
            return null;
        }

        try {
            // Check cache first
            const cacheKey = `repplus_${tabId || 'current'}`;
            const cached = this.cache.get(cacheKey);
            if (cached && (Date.now() - cached.timestamp) < 30000) { // 30s cache
                return cached.data;
            }

            // Method 1: Read from rep+ storage
            const repData = await this.readRepPlusStorage(tabId);
            
            if (repData) {
                // Cache the results
                this.cache.set(cacheKey, {
                    data: repData,
                    timestamp: Date.now()
                });
                return repData;
            }

            // Method 2: Request data directly from rep+ extension
            const directData = await this.requestRepPlusData(tabId);
            
            if (directData) {
                this.cache.set(cacheKey, {
                    data: directData,
                    timestamp: Date.now()
                });
            }

            return directData;

        } catch (error) {
            console.error('Failed to get rep+ results:', error);
            return null;
        }
    }

    /**
     * Read rep+ results from shared browser storage
     */
    async readRepPlusStorage(tabId = null) {
        try {
            const storageKeys = [
                'rep_plus_endpoints',
                'rep_plus_secrets', 
                'rep_plus_parameters',
                'rep_plus_session_data',
                `rep_plus_tab_${tabId}`
            ];

            const storage = await chrome.storage.local.get(storageKeys);
            
            if (Object.keys(storage).length === 0) {
                return null;
            }

            // Transform rep+ data to our format
            return {
                endpoints: this.transformRepPlusEndpoints(storage.rep_plus_endpoints || []),
                secrets: this.transformRepPlusSecrets(storage.rep_plus_secrets || []),
                parameters: storage.rep_plus_parameters || [],
                sessionData: storage.rep_plus_session_data || {},
                source: 'rep_plus_storage',
                timestamp: Date.now()
            };

        } catch (error) {
            console.error('Failed to read rep+ storage:', error);
            return null;
        }
    }

    /**
     * Request data directly from rep+ extension
     */
    async requestRepPlusData(tabId = null) {
        if (!this.repPlusExtensionId) {
            return null;
        }
        return new Promise((resolve) => {
            try {
                chrome.runtime.sendMessage(this.repPlusExtensionId, {
                    action: 'getAnalysisResults',
                    tabId: tabId,
                    source: 'js-security-extractor'
                }, (response) => {
                    if (chrome.runtime.lastError) {
                        console.warn('Rep+ request failed:', chrome.runtime.lastError);
                        resolve(null);
                    } else {
                        resolve(response);
                    }
                });
            } catch (error) {
                console.error('Rep+ message sending failed:', error);
                resolve(null);
            }
        });
    }

    /**
     * Transform rep+ endpoint format to our format
     */
    transformRepPlusEndpoints(repEndpoints) {
        return repEndpoints.map(endpoint => ({
            url: endpoint.endpoint || endpoint.url,
            method: endpoint.method || 'GET',
            source: endpoint.source || 'rep_plus',
            confidence: this.mapRepPlusConfidence(endpoint.confidence),
            type: 'rep_plus_endpoint',
            sourceFile: endpoint.sourceFile,
            line: endpoint.line,
            context: endpoint.context,
            extractor: 'rep_plus'
        }));
    }

    /**
     * Transform rep+ secret format to our format
     */
    transformRepPlusSecrets(repSecrets) {
        return repSecrets.map(secret => ({
            value: secret.value || secret.match,
            type: secret.type || secret.rule || 'unknown',
            source: secret.source || 'rep_plus',
            confidence: this.mapRepPlusConfidence(secret.confidence),
            sourceFile: secret.sourceFile,
            line: secret.line,
            context: secret.context,
            extractor: 'rep_plus_kingfisher'
        }));
    }

    /**
     * Map rep+ confidence levels to our format
     */
    mapRepPlusConfidence(repConfidence) {
        const mapping = {
            'high': 'high',
            'medium': 'medium',
            'low': 'low',
            'info': 'low'
        };
        return mapping[repConfidence] || 'medium';
    }

    /**
     * Merge rep+ results with your analysis results
     */
    mergeResults(yourResults, repPlusResults) {
        if (!repPlusResults) {
            return yourResults;
        }

        const merged = {
            ...yourResults,
            rep_plus_data: repPlusResults,
            merged_analysis: {
                endpoints: {
                    your_extraction: yourResults.endpoints || [],
                    rep_plus: repPlusResults.endpoints || [],
                    combined: this.deduplicateEndpoints([
                        ...(yourResults.endpoints || []),
                        ...(repPlusResults.endpoints || [])
                    ])
                },
                secrets: {
                    your_extraction: yourResults.secrets || [],
                    rep_plus_kingfisher: repPlusResults.secrets || [],
                    combined: this.deduplicateSecrets([
                        ...(yourResults.secrets || []),
                        ...(repPlusResults.secrets || [])
                    ])
                },
                stats: {
                    total_endpoints: 0,
                    total_secrets: 0,
                    rep_plus_endpoints: repPlusResults.endpoints?.length || 0,
                    rep_plus_secrets: repPlusResults.secrets?.length || 0,
                    your_endpoints: yourResults.endpoints?.length || 0,
                    your_secrets: yourResults.secrets?.length || 0
                }
            }
        };

        // Update stats
        merged.merged_analysis.stats.total_endpoints = merged.merged_analysis.endpoints.combined.length;
        merged.merged_analysis.stats.total_secrets = merged.merged_analysis.secrets.combined.length;

        return merged;
    }

    /**
     * Extract script-like import hints from rep+ outputs.
     * Returned shape matches extension dependency objects.
     */
    extractScriptImportHints(repPlusResults, baseUrl = null) {
        if (!repPlusResults || typeof repPlusResults !== 'object') {
            return [];
        }

        const candidates = [];
        const endpointRows = Array.isArray(repPlusResults.endpoints) ? repPlusResults.endpoints : [];
        const parameterRows = Array.isArray(repPlusResults.parameters) ? repPlusResults.parameters : [];

        for (const endpoint of endpointRows) {
            const candidate = endpoint?.url || endpoint?.endpoint || endpoint?.path;
            if (typeof candidate === 'string' && candidate.trim()) {
                candidates.push(candidate.trim());
            }
        }

        for (const parameter of parameterRows) {
            const parameterValues = [parameter?.url, parameter?.value, parameter?.path];
            for (const value of parameterValues) {
                if (typeof value === 'string' && value.trim()) {
                    candidates.push(value.trim());
                }
            }
        }

        const seen = new Set();
        const hints = [];

        for (const candidate of candidates) {
            const resolved = this.resolveCandidateUrl(candidate, baseUrl);
            if (!resolved || !this.isLikelyScriptResource(resolved)) {
                continue;
            }
            if (seen.has(resolved)) {
                continue;
            }
            seen.add(resolved);
            hints.push({
                url: candidate,
                type: 'rep_plus_hint',
                resolvedUrl: resolved,
                source: 'rep_plus'
            });
        }

        return hints;
    }

    resolveCandidateUrl(candidate, baseUrl = null) {
        try {
            if (candidate.startsWith('http://') || candidate.startsWith('https://')) {
                return candidate;
            }
            if (baseUrl) {
                return new URL(candidate, baseUrl).href;
            }
            return candidate;
        } catch (error) {
            return candidate;
        }
    }

    isLikelyScriptResource(url) {
        if (!url || typeof url !== 'string') {
            return false;
        }
        const normalized = url.toLowerCase();
        const query = normalized.includes('?') ? normalized.slice(normalized.indexOf('?')) : '';

        if (
            normalized.includes('.js') ||
            normalized.includes('.mjs') ||
            normalized.includes('.cjs') ||
            normalized.includes('/chunks/') ||
            normalized.includes('/chunk/') ||
            normalized.includes('/_next/static/')
        ) {
            return true;
        }
        return query.includes('.js');
    }

    summarize(repPlusResults) {
        if (!repPlusResults) {
            return {
                available: this.isRepPlusAvailable,
                extensionIdConfigured: !!this.repPlusExtensionId,
                source: null,
                endpointCount: 0,
                secretCount: 0,
                parameterCount: 0
            };
        }
        return {
            available: this.isRepPlusAvailable,
            extensionIdConfigured: !!this.repPlusExtensionId,
            source: repPlusResults.source || null,
            endpointCount: Array.isArray(repPlusResults.endpoints) ? repPlusResults.endpoints.length : 0,
            secretCount: Array.isArray(repPlusResults.secrets) ? repPlusResults.secrets.length : 0,
            parameterCount: Array.isArray(repPlusResults.parameters) ? repPlusResults.parameters.length : 0
        };
    }

    /**
     * Deduplicate endpoints by URL and method
     */
    deduplicateEndpoints(endpoints) {
        const seen = new Set();
        const unique = [];

        for (const endpoint of endpoints) {
            const key = `${endpoint.method || 'GET'}:${endpoint.url}`;
            if (!seen.has(key)) {
                seen.add(key);
                unique.push(endpoint);
            }
        }

        return unique;
    }

    /**
     * Deduplicate secrets by value
     */
    deduplicateSecrets(secrets) {
        const seen = new Set();
        const unique = [];

        for (const secret of secrets) {
            const key = secret.value;
            if (!seen.has(key) && key) {
                seen.add(key);
                unique.push(secret);
            }
        }

        return unique;
    }

    /**
     * Set up periodic sync with rep+ data
     */
    setupPeriodicSync() {
        // Sync every 30 seconds if rep+ is available
        setInterval(async () => {
            if (this.isRepPlusAvailable) {
                // Clear old cache entries
                this.clearOldCache();
            }
        }, 30000);
    }

    /**
     * Clear cache entries older than 5 minutes
     */
    clearOldCache() {
        const now = Date.now();
        for (const [key, value] of this.cache.entries()) {
            if (now - value.timestamp > 300000) { // 5 minutes
                this.cache.delete(key);
            }
        }
    }

    /**
     * Check if rep+ should handle certain analysis (to avoid duplication)
     */
    shouldSkipAnalysis(analysisType, jsContent) {
        if (!this.isRepPlusAvailable) {
            return false;
        }

        // Let rep+ handle basic endpoint detection if it's available and working
        if (analysisType === 'basic_endpoints' && jsContent.length < 100000) {
            return true;
        }

        // Let rep+ handle Kingfisher secret detection
        if (analysisType === 'basic_secrets') {
            return true;
        }

        return false;
    }

    /**
     * Send notification to rep+ extension (if needed)
     */
    async notifyRepPlus(message) {
        if (!this.isRepPlusAvailable) {
            return false;
        }

        try {
            return new Promise((resolve) => {
                chrome.runtime.sendMessage(this.repPlusExtensionId, {
                    action: 'notification',
                    message: message,
                    source: 'js-security-extractor'
                }, (response) => {
                    resolve(!chrome.runtime.lastError);
                });
            });
        } catch (error) {
            console.error('Failed to notify rep+:', error);
            return false;
        }
    }
}

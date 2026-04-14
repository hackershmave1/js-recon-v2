/**
 * Enhanced Analyzer Module
 * 
 * Coordinates analysis between local extraction, rep+ integration, and API processing.
 * Provides a unified interface for comprehensive JavaScript security analysis.
 */

export class EnhancedAnalyzer {
    constructor(repPlusBridge, batchUploader) {
        this.repPlusBridge = repPlusBridge;
        this.batchUploader = batchUploader;
        this.analysisCache = new Map();
        this.processingQueue = [];
        this.isProcessing = false;
    }

    /**
     * Perform comprehensive analysis of JavaScript content
     * 
     * @param {Object} fileData - File data object
     * @param {Object} options - Analysis options
     * @returns {Object} Comprehensive analysis results
     */
    async analyzeFile(fileData, options = {}) {
        const startTime = Date.now();
        
        const analysis = {
            metadata: {
                url: fileData.url,
                size: fileData.content.length,
                contentHash: fileData.contentHash,
                analysisTimestamp: new Date().toISOString()
            },
            local_analysis: {},
            rep_plus_analysis: {},
            api_analysis: {},
            merged_results: {},
            processing_stats: {}
        };

        try {
            // 1. Get rep+ results if available (fast, local)
            if (this.repPlusBridge.isRepPlusAvailable && !options.skipRepPlus) {
                analysis.rep_plus_analysis = await this.getRepPlusAnalysis(fileData);
            }

            // 2. Perform local basic analysis (fast, immediate feedback)
            analysis.local_analysis = await this.performLocalAnalysis(fileData, options);

            // 3. Send to API for comprehensive analysis (slower, thorough)
            if (options.useAPI !== false) {
                analysis.api_analysis = await this.sendToAPIForAnalysis(fileData, options);
            }

            // 4. Merge all results
            analysis.merged_results = await this.mergeAllAnalysis(
                analysis.local_analysis,
                analysis.rep_plus_analysis,
                analysis.api_analysis
            );

            // 5. Calculate processing stats
            analysis.processing_stats = {
                total_time_ms: Date.now() - startTime,
                extractors_used: this.getExtractorsUsed(analysis),
                total_endpoints: analysis.merged_results.endpoints?.length || 0,
                total_secrets: analysis.merged_results.secrets?.length || 0,
                total_dependencies: analysis.merged_results.dependencies?.length || 0,
                rep_plus_available: this.repPlusBridge.isRepPlusAvailable,
                api_processed: !!analysis.api_analysis.success
            };

            // Cache the results
            this.cacheAnalysis(fileData.contentHash, analysis);

            return analysis;

        } catch (error) {
            console.error('Enhanced analysis failed:', error);
            
            analysis.error = {
                message: error.message,
                timestamp: new Date().toISOString(),
                processing_time_ms: Date.now() - startTime
            };

            return analysis;
        }
    }

    /**
     * Get analysis results from rep+ if available
     */
    async getRepPlusAnalysis(fileData) {
        try {
            const repResults = await this.repPlusBridge.getRepPlusResults();
            
            if (repResults) {
                return {
                    success: true,
                    endpoints: repResults.endpoints || [],
                    secrets: repResults.secrets || [],
                    parameters: repResults.parameters || [],
                    source: 'rep_plus',
                    timestamp: repResults.timestamp
                };
            }

            return { success: false, reason: 'No rep+ data available' };

        } catch (error) {
            console.error('Rep+ analysis failed:', error);
            return { success: false, error: error.message };
        }
    }

    /**
     * Perform local analysis for immediate feedback
     */
    async performLocalAnalysis(fileData, options) {
        try {
            const localResults = {
                endpoints: [],
                secrets: [],
                dependencies: [],
                patterns: [],
                source: 'local_analysis'
            };

            // Basic regex-based endpoint detection
            const endpoints = this.extractBasicEndpoints(fileData.content);
            localResults.endpoints = endpoints;

            // Basic secret pattern detection
            const secrets = this.extractBasicSecrets(fileData.content);
            localResults.secrets = secrets;

            // Basic dependency extraction
            const dependencies = this.extractBasicDependencies(fileData.content, fileData.url);
            localResults.dependencies = dependencies;

            // Basic security patterns
            const patterns = this.extractSecurityPatterns(fileData.content);
            localResults.patterns = patterns;

            return {
                success: true,
                ...localResults,
                stats: {
                    endpoints_found: endpoints.length,
                    secrets_found: secrets.length,
                    dependencies_found: dependencies.length,
                    patterns_found: patterns.length
                }
            };

        } catch (error) {
            console.error('Local analysis failed:', error);
            return { success: false, error: error.message };
        }
    }

    /**
     * Send file to API for comprehensive analysis
     */
    async sendToAPIForAnalysis(fileData, options) {
        try {
            // Format file data for API
            const apiPayload = {
                metadata: { sessionId: this.generateSessionId() },
                files: [{
                    url: fileData.url,
                    contentHash: fileData.contentHash,
                    sessionId: this.generateSessionId(),
                    capturedAt: new Date().toISOString(),
                    contentType: 'application/javascript',
                    contentLength: fileData.content.length,
                    content: fileData.content,
                    sourceMapUrl: fileData.sourceMapUrl || null,
                    dependencies: fileData.dependencies || []
                }]
            };

            // Send to your enhanced API
            const response = await this.batchUploader.uploadFiles(apiPayload);
            
            if (response.success) {
                // Wait a bit for processing, then get comprehensive results
                await this.delay(2000); // Wait for background processing
                
                const analysisResults = await this.getAPIAnalysisResults(response.sessionId);
                
                return {
                    success: true,
                    sessionId: response.sessionId,
                    fileIds: response.fileIds,
                    analysis: analysisResults,
                    source: 'comprehensive_api'
                };
            }

            return { success: false, error: 'API upload failed' };

        } catch (error) {
            console.error('API analysis failed:', error);
            return { success: false, error: error.message };
        }
    }

    /**
     * Get comprehensive analysis results from API
     */
    async getAPIAnalysisResults(sessionId) {
        try {
            const response = await fetch(`${this.batchUploader.apiEndpoint.replace('/save-files', '')}/sessions/${sessionId}/analysis`);
            
            if (response.ok) {
                return await response.json();
            }

            return null;

        } catch (error) {
            console.error('Failed to get API analysis results:', error);
            return null;
        }
    }

    /**
     * Merge analysis results from all sources
     */
    async mergeAllAnalysis(localAnalysis, repPlusAnalysis, apiAnalysis) {
        const merged = {
            endpoints: [],
            secrets: [],
            dependencies: [],
            patterns: [],
            sourcemap_data: null,
            reconstructed_files: [],
            confidence_scores: {}
        };

        // Collect all endpoints
        if (localAnalysis.success) {
            merged.endpoints.push(...(localAnalysis.endpoints || []));
            merged.secrets.push(...(localAnalysis.secrets || []));
            merged.dependencies.push(...(localAnalysis.dependencies || []));
        }

        if (repPlusAnalysis.success) {
            merged.endpoints.push(...(repPlusAnalysis.endpoints || []));
            merged.secrets.push(...(repPlusAnalysis.secrets || []));
        }

        if (apiAnalysis.success && apiAnalysis.analysis) {
            const apiData = apiAnalysis.analysis.analysis || {};
            merged.endpoints.push(...(apiData.endpoints || []));
            merged.secrets.push(...(apiData.secrets || []));
            merged.dependencies.push(...(apiData.dependencies || []));
            merged.sourcemap_data = apiData.sourcemap;
            merged.reconstructed_files = apiData.reconstructed_files || [];
        }

        // Deduplicate and score
        merged.endpoints = this.deduplicateEndpoints(merged.endpoints);
        merged.secrets = this.deduplicateSecrets(merged.secrets);
        merged.dependencies = this.deduplicateDependencies(merged.dependencies);

        // Calculate confidence scores
        merged.confidence_scores = this.calculateConfidenceScores(merged);

        return merged;
    }

    /**
     * Basic endpoint extraction using regex patterns
     */
    extractBasicEndpoints(content) {
        const endpoints = [];
        const patterns = [
            { pattern: /fetch\s*\(\s*['"`]([^'"`]+)['"`]/g, method: 'GET', type: 'fetch_call' },
            { pattern: /axios\.(get|post|put|delete|patch)\s*\(\s*['"`]([^'"`]+)['"`]/g, type: 'axios_call' },
            { pattern: /XMLHttpRequest.*?open\s*\(\s*['"`]([^'"`]+)['"`]\s*,\s*['"`]([^'"`]+)['"`]/g, type: 'xhr_call' },
            { pattern: /['"`](/api/[^'"`\s]+)['"`]/g, method: 'GET', type: 'api_path' },
            { pattern: /['"`](https?:\/\/[^'"`\s]+)['"`]/g, method: 'GET', type: 'absolute_url' }
        ];

        patterns.forEach(({ pattern, method, type }) => {
            let match;
            while ((match = pattern.exec(content)) !== null) {
                const url = match[2] || match[1];
                const detectedMethod = match[1]?.toUpperCase() || method || 'GET';
                
                endpoints.push({
                    url: url,
                    method: detectedMethod,
                    type: type,
                    line: content.substring(0, match.index).split('\n').length,
                    context: content.substring(Math.max(0, match.index - 50), match.index + 100),
                    confidence: 'medium',
                    source: 'local_regex'
                });
            }
        });

        return endpoints;
    }

    /**
     * Basic secret extraction using regex patterns
     */
    extractBasicSecrets(content) {
        const secrets = [];
        const patterns = [
            { pattern: /(['"`])([A-Za-z0-9_-]{32,})(['"`])/g, type: 'potential_key' },
            { pattern: /(api[_-]?key|apikey)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi, type: 'api_key' },
            { pattern: /(secret|password|pwd)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi, type: 'credential' },
            { pattern: /(token|bearer)\s*[:=]\s*['"`]([^'"`]+)['"`]/gi, type: 'token' },
            { pattern: /sk_live_[a-zA-Z0-9]{24,}/g, type: 'stripe_secret' },
            { pattern: /pk_live_[a-zA-Z0-9]{24,}/g, type: 'stripe_public' }
        ];

        patterns.forEach(({ pattern, type }) => {
            let match;
            while ((match = pattern.exec(content)) !== null) {
                const value = match[2] || match[0];
                
                if (value && value.length > 8) {
                    secrets.push({
                        value: value,
                        type: type,
                        line: content.substring(0, match.index).split('\n').length,
                        context: content.substring(Math.max(0, match.index - 30), match.index + 50),
                        confidence: 'medium',
                        source: 'local_regex'
                    });
                }
            }
        });

        return secrets;
    }

    /**
     * Basic dependency extraction
     */
    extractBasicDependencies(content, baseUrl) {
        const dependencies = [];
        const patterns = [
            { pattern: /import\s+.*?\s+from\s+['"`]([^'"`]+)['"`]/g, type: 'es6_import' },
            { pattern: /import\s*\(\s*['"`]([^'"`]+)['"`]\s*\)/g, type: 'dynamic_import' },
            { pattern: /require\s*\(\s*['"`]([^'"`]+)['"`]\s*\)/g, type: 'commonjs' }
        ];

        patterns.forEach(({ pattern, type }) => {
            let match;
            while ((match = pattern.exec(content)) !== null) {
                dependencies.push({
                    path: match[1],
                    type: type,
                    line: content.substring(0, match.index).split('\n').length,
                    resolved_url: this.resolveUrl(match[1], baseUrl)
                });
            }
        });

        return dependencies;
    }

    /**
     * Extract security-relevant patterns
     */
    extractSecurityPatterns(content) {
        const patterns = [];
        const securityPatterns = [
            { pattern: /eval\s*\(/g, type: 'unsafe_eval', severity: 'high' },
            { pattern: /innerHTML\s*=\s*[^;]*\+/g, type: 'xss_vulnerable', severity: 'medium' },
            { pattern: /document\.write\s*\(/g, type: 'document_write', severity: 'medium' },
            { pattern: /localStorage\.setItem\s*\(\s*['"`][^'"`]*password[^'"`]*['"`]/gi, type: 'password_storage', severity: 'high' }
        ];

        securityPatterns.forEach(({ pattern, type, severity }) => {
            let match;
            while ((match = pattern.exec(content)) !== null) {
                patterns.push({
                    type: type,
                    severity: severity,
                    line: content.substring(0, match.index).split('\n').length,
                    context: content.substring(Math.max(0, match.index - 30), match.index + 50)
                });
            }
        });

        return patterns;
    }

    /**
     * Utility functions for deduplication and processing
     */
    deduplicateEndpoints(endpoints) {
        const seen = new Set();
        return endpoints.filter(endpoint => {
            const key = `${endpoint.method}:${endpoint.url}`;
            if (!seen.has(key)) {
                seen.add(key);
                return true;
            }
            return false;
        });
    }

    deduplicateSecrets(secrets) {
        const seen = new Set();
        return secrets.filter(secret => {
            if (!seen.has(secret.value)) {
                seen.add(secret.value);
                return true;
            }
            return false;
        });
    }

    deduplicateDependencies(dependencies) {
        const seen = new Set();
        return dependencies.filter(dep => {
            if (!seen.has(dep.path)) {
                seen.add(dep.path);
                return true;
            }
            return false;
        });
    }

    calculateConfidenceScores(merged) {
        return {
            endpoints: this.calculateEndpointConfidence(merged.endpoints),
            secrets: this.calculateSecretConfidence(merged.secrets),
            overall: this.calculateOverallConfidence(merged)
        };
    }

    calculateEndpointConfidence(endpoints) {
        let totalScore = 0;
        endpoints.forEach(endpoint => {
            switch(endpoint.confidence) {
                case 'high': totalScore += 3; break;
                case 'medium': totalScore += 2; break;
                case 'low': totalScore += 1; break;
            }
        });
        return endpoints.length > 0 ? totalScore / (endpoints.length * 3) : 0;
    }

    calculateSecretConfidence(secrets) {
        let totalScore = 0;
        secrets.forEach(secret => {
            switch(secret.confidence) {
                case 'high': totalScore += 3; break;
                case 'medium': totalScore += 2; break;
                case 'low': totalScore += 1; break;
            }
        });
        return secrets.length > 0 ? totalScore / (secrets.length * 3) : 0;
    }

    calculateOverallConfidence(merged) {
        const endpointConf = this.calculateEndpointConfidence(merged.endpoints);
        const secretConf = this.calculateSecretConfidence(merged.secrets);
        return (endpointConf + secretConf) / 2;
    }

    resolveUrl(path, baseUrl) {
        if (path.startsWith('http')) return path;
        if (path.startsWith('/')) return new URL(baseUrl).origin + path;
        return new URL(path, baseUrl).href;
    }

    getExtractorsUsed(analysis) {
        const extractors = [];
        if (analysis.local_analysis.success) extractors.push('local_regex');
        if (analysis.rep_plus_analysis.success) extractors.push('rep_plus');
        if (analysis.api_analysis.success) extractors.push('comprehensive_api');
        return extractors;
    }

    generateSessionId() {
        return 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    }

    cacheAnalysis(hash, analysis) {
        this.analysisCache.set(hash, {
            data: analysis,
            timestamp: Date.now()
        });

        // Clean old cache entries
        if (this.analysisCache.size > 100) {
            const oldestKey = this.analysisCache.keys().next().value;
            this.analysisCache.delete(oldestKey);
        }
    }

    delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
}
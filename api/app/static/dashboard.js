// JavaScript Security Extractor Dashboard
class SecurityDashboard {
    constructor() {
        this.apiBase = window.location.origin;
        this.currentResults = null;
        this.activeTab = 'analysis';
        this.runningFileAnalyses = new Set();
        this.runningSessionAnalyses = new Set();
        this.sessionAnalysisProgress = new Map();
        this.sessionProgressPollers = new Map();
        this.sessionPollingInFlight = new Set();
        this.sessionCompletionNotified = new Set();
        this.selectedFileIds = new Set();
        this.selectedSessionIds = new Set();
        this.visibleFileIds = [];
        this.visibleSessionIds = [];
        this.fileStatusPoller = null;
        this.fileStatusPollingInFlight = false;
        this.fileStatusSnapshot = new Map();
        this.activeFilesSessionId = null;
        this.activeFilesSessionName = null;
        this.filesFilter = {
            query: '',
            status: 'all',
        };
        this.sessionsFilter = {
            query: '',
            status: 'all',
        };
        this.filesFilterDebounce = null;
        this.sessionsFilterDebounce = null;
        this.resultsContext = null;
        this.failureUtils = window.DashboardFailureUtils || null;
        this.sessionAnalyzeDefaultsStorageKey = 'dashboard.sessionAnalyzeDefaults.v1';
        this.activeSessionAnalyzeTargetId = null;
        this.sessionAnalyzeModal = null;
        this.activeFileAnalyzeTargetId = null;
        this.fileAnalyzeModal = null;
        this.createSessionModal = null;
        this.reconJobPollers = new Map();
        this.reconSessionProgress = new Map();
        this.reconPollingInFlight = new Set();
        this.routePaths = {
            analysis: '/analysis',
            files: '/view_files',
            sessions: '/sessions',
        };
        
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setupRouting();
        this.checkAPIStatus();
        this.loadStatistics();
        this.setupDragAndDrop();
        this.validateInput();
        this.handleLocationRoute({ replaceState: true }).catch((error) => {
            console.error('Route handling error:', error);
            this.switchTab('analysis', { pushHistory: false });
        });
    }

    setupRouting() {
        window.addEventListener('popstate', () => {
            this.handleLocationRoute({ replaceState: true }).catch((error) => {
                console.error('Route handling error:', error);
            });
        });
    }

    resolveTabFromPath(pathname) {
        if (pathname === '/sessions') return 'sessions';
        if (pathname === '/view_files') return 'files';
        return 'analysis';
    }

    updateBrowserRoute(tabName, options = {}) {
        const { replace = false, query = {} } = options;
        const path = this.routePaths[tabName] || this.routePaths.analysis;
        const params = new URLSearchParams();

        Object.entries(query).forEach(([key, value]) => {
            if (value !== null && value !== undefined && `${value}`.trim() !== '') {
                params.set(key, `${value}`);
            }
        });

        const url = params.toString() ? `${path}?${params.toString()}` : path;
        const state = { tab: tabName, query };
        if (replace) {
            window.history.replaceState(state, '', url);
        } else {
            window.history.pushState(state, '', url);
        }
    }

    async handleLocationRoute(options = {}) {
        const { replaceState = false } = options;
        const pathname = window.location.pathname || '/';
        const params = new URLSearchParams(window.location.search || '');
        const tabName = this.resolveTabFromPath(pathname);
        const fileId = params.get('file_id');
        const sessionId = params.get('session_id');
        const sessionName = params.get('session_name');

        if (tabName === 'files' && sessionId) {
            this.activeFilesSessionId = sessionId;
            this.activeFilesSessionName = sessionName || `Session ${this.shortId(sessionId)}`;
        } else if (tabName !== 'files') {
            this.activeFilesSessionId = null;
            this.activeFilesSessionName = null;
        }

        this.switchTab(tabName, { pushHistory: false });

        if (tabName === 'analysis' && fileId) {
            await this.viewStoredAnalysis(fileId, {
                updateRoute: false,
                silent: true,
            });
            return;
        }

        if (replaceState) {
            const query = {};
            if (tabName === 'files' && this.activeFilesSessionId) {
                query.session_id = this.activeFilesSessionId;
                query.session_name = this.activeFilesSessionName || '';
            }
            this.updateBrowserRoute(tabName, { replace: true, query });
        }
    }

    setupEventListeners() {
        // Analysis form submission
        document.getElementById('analysis-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.performAnalysis();
        });

        // Form input handling
        document.getElementById('js-content').addEventListener('change', () => {
            this.validateInput();
        });
        document.getElementById('js-content').addEventListener('input', () => {
            this.validateInput();
        });
        document.getElementById('js-url').addEventListener('change', () => {
            this.validateInput();
        });
        document.getElementById('js-url').addEventListener('input', () => {
            this.validateInput();
        });

        const filesQuery = document.getElementById('files-filter-query');
        const filesStatus = document.getElementById('files-filter-status');
        const filesClear = document.getElementById('files-filter-clear');
        if (filesQuery) {
            filesQuery.addEventListener('input', () => {
                this.filesFilter.query = filesQuery.value || '';
                this.scheduleFilesFilterApply();
            });
        }
        if (filesStatus) {
            filesStatus.addEventListener('change', () => {
                this.filesFilter.status = filesStatus.value || 'all';
                this.scheduleFilesFilterApply();
            });
        }
        if (filesClear) {
            filesClear.addEventListener('click', () => this.clearFilesFilters());
        }

        const sessionsQuery = document.getElementById('sessions-filter-query');
        const sessionsStatus = document.getElementById('sessions-filter-status');
        const sessionsClear = document.getElementById('sessions-filter-clear');
        if (sessionsQuery) {
            sessionsQuery.addEventListener('input', () => {
                this.sessionsFilter.query = sessionsQuery.value || '';
                this.scheduleSessionsFilterApply();
            });
        }
        if (sessionsStatus) {
            sessionsStatus.addEventListener('change', () => {
                this.sessionsFilter.status = sessionsStatus.value || 'all';
                this.scheduleSessionsFilterApply();
            });
        }
        if (sessionsClear) {
            sessionsClear.addEventListener('click', () => this.clearSessionsFilters());
        }

        const sessionAnalyzeQuickBtn = document.getElementById('session-analyze-run-quick');
        if (sessionAnalyzeQuickBtn) {
            sessionAnalyzeQuickBtn.addEventListener('click', () => this.submitSessionAnalyzeFromModal('quick'));
        }

        const sessionAnalyzeForm = document.getElementById('session-analyze-form');
        if (sessionAnalyzeForm) {
            sessionAnalyzeForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.submitSessionAnalyzeFromModal('advanced');
            });
        }

        const sessionAnalyzeType = document.getElementById('session-analyze-analysis-type');
        if (sessionAnalyzeType) {
            sessionAnalyzeType.addEventListener('change', () => this.syncSessionAnalyzeExtractorsWithType());
        }

        const sessionAnalyzeModal = document.getElementById('sessionAnalyzeConfigModal');
        if (sessionAnalyzeModal) {
            sessionAnalyzeModal.addEventListener('hidden.bs.modal', () => {
                this.activeSessionAnalyzeTargetId = null;
                this.setSessionAnalyzeModalBusy(false);
            });
        }

        const createSessionButton = document.getElementById('create-session-btn');
        if (createSessionButton) {
            createSessionButton.addEventListener('click', () => this.openCreateSessionModal());
        }

        const createSessionForm = document.getElementById('create-session-form');
        if (createSessionForm) {
            createSessionForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.submitCreateSessionFromModal();
            });
        }

        const createSessionModal = document.getElementById('createSessionModal');
        if (createSessionModal) {
            createSessionModal.addEventListener('hidden.bs.modal', () => {
                this.setCreateSessionModalBusy(false);
            });
        }

        const fileAnalyzeQuickBtn = document.getElementById('file-analyze-run-quick');
        if (fileAnalyzeQuickBtn) {
            fileAnalyzeQuickBtn.addEventListener('click', () => this.submitFileAnalyzeFromModal('quick'));
        }

        const fileAnalyzeForm = document.getElementById('file-analyze-form');
        if (fileAnalyzeForm) {
            fileAnalyzeForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.submitFileAnalyzeFromModal('advanced');
            });
        }

        const fileAnalyzeType = document.getElementById('file-analyze-analysis-type');
        if (fileAnalyzeType) {
            fileAnalyzeType.addEventListener('change', () => this.syncFileAnalyzeExtractorsWithType());
        }

        const fileAnalyzeModal = document.getElementById('fileAnalyzeConfigModal');
        if (fileAnalyzeModal) {
            fileAnalyzeModal.addEventListener('hidden.bs.modal', () => {
                this.activeFileAnalyzeTargetId = null;
                this.setFileAnalyzeModalBusy(false);
            });
        }
    }

    scheduleFilesFilterApply() {
        if (this.filesFilterDebounce) {
            window.clearTimeout(this.filesFilterDebounce);
        }
        this.filesFilterDebounce = window.setTimeout(() => {
            this.filesFilterDebounce = null;
            if (this.activeTab === 'files') {
                this.loadFiles();
            }
        }, 180);
    }

    scheduleSessionsFilterApply() {
        if (this.sessionsFilterDebounce) {
            window.clearTimeout(this.sessionsFilterDebounce);
        }
        this.sessionsFilterDebounce = window.setTimeout(() => {
            this.sessionsFilterDebounce = null;
            if (this.activeTab === 'sessions') {
                this.loadSessions();
            }
        }, 180);
    }

    clearFilesFilters() {
        this.filesFilter = { query: '', status: 'all' };
        const filesQuery = document.getElementById('files-filter-query');
        const filesStatus = document.getElementById('files-filter-status');
        if (filesQuery) filesQuery.value = '';
        if (filesStatus) filesStatus.value = 'all';
        if (this.activeTab === 'files') {
            this.loadFiles();
        }
    }

    clearSessionsFilters() {
        this.sessionsFilter = { query: '', status: 'all' };
        const sessionsQuery = document.getElementById('sessions-filter-query');
        const sessionsStatus = document.getElementById('sessions-filter-status');
        if (sessionsQuery) sessionsQuery.value = '';
        if (sessionsStatus) sessionsStatus.value = 'all';
        if (this.activeTab === 'sessions') {
            this.loadSessions();
        }
    }

    getSessionAnalyzeDefaultOptions() {
        return {
            run_mode: 'advanced',
            analysis_type: 'comprehensive',
            include_sourcemap: true,
            resolve_urls: true,
            use_rep_endpoints: true,
            use_rep_secrets: true,
            use_jsluice_endpoints: false,
            use_jsluice_secrets: false,
            include_reconstructed_sources: true,
            continue_on_error: true,
            max_files_to_analyze: null,
            max_failures: null,
            per_file_timeout_ms: null,
            retry_attempts: 0,
        };
    }

    loadSessionAnalyzeDefaults() {
        const defaults = this.getSessionAnalyzeDefaultOptions();
        try {
            const raw = window.localStorage.getItem(this.sessionAnalyzeDefaultsStorageKey);
            if (!raw) return defaults;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return defaults;
            return { ...defaults, ...parsed };
        } catch (_error) {
            return defaults;
        }
    }

    saveSessionAnalyzeDefaults(options) {
        if (!options || typeof options !== 'object') return;
        const payload = { ...this.getSessionAnalyzeDefaultOptions(), ...options };
        try {
            window.localStorage.setItem(this.sessionAnalyzeDefaultsStorageKey, JSON.stringify(payload));
        } catch (_error) {
            // Ignore local storage write failures (private mode/quota).
        }
    }

    populateSessionAnalyzeModal(sessionId) {
        const targetLabel = document.getElementById('session-analyze-target-name');
        const typeSelect = document.getElementById('session-analyze-analysis-type');
        const includeSourcemap = document.getElementById('session-analyze-include-sourcemap');
        const resolveUrls = document.getElementById('session-analyze-resolve-urls');
        const useRepEndpoints = document.getElementById('session-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('session-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('session-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('session-analyze-use-jsluice-secrets');
        const includeReconstructed = document.getElementById('session-analyze-include-reconstructed');
        const continueOnError = document.getElementById('session-analyze-continue-on-error');
        const maxFiles = document.getElementById('session-analyze-max-files');
        const maxFailures = document.getElementById('session-analyze-max-failures');
        const timeoutMs = document.getElementById('session-analyze-timeout-ms');
        const retryAttempts = document.getElementById('session-analyze-retry-attempts');

        const defaults = this.loadSessionAnalyzeDefaults();
        const sessionName = this.getSessionDisplayName(sessionId);
        if (targetLabel) {
            targetLabel.textContent = sessionName;
        }
        if (typeSelect) typeSelect.value = defaults.analysis_type || 'comprehensive';
        if (includeSourcemap) includeSourcemap.checked = Boolean(defaults.include_sourcemap);
        if (resolveUrls) resolveUrls.checked = Boolean(defaults.resolve_urls);
        if (useRepEndpoints) useRepEndpoints.checked = Boolean(defaults.use_rep_endpoints);
        if (useRepSecrets) useRepSecrets.checked = Boolean(defaults.use_rep_secrets);
        if (useJSluiceEndpoints) useJSluiceEndpoints.checked = Boolean(defaults.use_jsluice_endpoints);
        if (useJSluiceSecrets) useJSluiceSecrets.checked = Boolean(defaults.use_jsluice_secrets);
        if (includeReconstructed) includeReconstructed.checked = Boolean(defaults.include_reconstructed_sources);
        if (continueOnError) continueOnError.checked = Boolean(defaults.continue_on_error);
        if (maxFiles) maxFiles.value = defaults.max_files_to_analyze || '';
        if (maxFailures) maxFailures.value = defaults.max_failures || '';
        if (timeoutMs) timeoutMs.value = defaults.per_file_timeout_ms || '';
        if (retryAttempts) retryAttempts.value = Number(defaults.retry_attempts || 0);
        this.syncSessionAnalyzeExtractorsWithType();
    }

    syncSessionAnalyzeExtractorsWithType() {
        const typeSelect = document.getElementById('session-analyze-analysis-type');
        const useRepEndpoints = document.getElementById('session-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('session-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('session-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('session-analyze-use-jsluice-secrets');
        if (!typeSelect || !useRepEndpoints || !useRepSecrets || !useJSluiceEndpoints || !useJSluiceSecrets) {
            return;
        }

        const analysisType = String(typeSelect.value || 'comprehensive').toLowerCase();
        const lockForJsluice = analysisType === 'jsluice';
        useRepEndpoints.disabled = lockForJsluice;
        useRepSecrets.disabled = lockForJsluice;
        useJSluiceEndpoints.disabled = lockForJsluice;
        useJSluiceSecrets.disabled = lockForJsluice;

        if (analysisType === 'jsluice') {
            useRepEndpoints.checked = false;
            useRepSecrets.checked = false;
            useJSluiceEndpoints.checked = true;
            useJSluiceSecrets.checked = true;
        }
    }

    parseOptionalPositiveInt(value, minimum, maximum) {
        if (value === null || value === undefined) return null;
        const normalized = `${value}`.trim();
        if (!normalized) return null;
        const parsed = Number.parseInt(normalized, 10);
        if (Number.isNaN(parsed) || parsed <= 0) return null;
        return Math.min(Math.max(parsed, minimum), maximum);
    }

    parseOptionalInt(value, minimum, maximum) {
        if (value === null || value === undefined) return null;
        const normalized = `${value}`.trim();
        if (!normalized) return null;
        const parsed = Number.parseInt(normalized, 10);
        if (Number.isNaN(parsed)) return null;
        return Math.min(Math.max(parsed, minimum), maximum);
    }

    generateClientUuid() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        // Fallback RFC4122-like v4 format if randomUUID is unavailable.
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
            const random = Math.floor(Math.random() * 16);
            const value = char === 'x' ? random : (random & 0x3) | 0x8;
            return value.toString(16);
        });
    }

    collectSessionAnalyzeModalOptions(runMode = 'advanced') {
        const typeSelect = document.getElementById('session-analyze-analysis-type');
        const includeSourcemap = document.getElementById('session-analyze-include-sourcemap');
        const resolveUrls = document.getElementById('session-analyze-resolve-urls');
        const useRepEndpoints = document.getElementById('session-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('session-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('session-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('session-analyze-use-jsluice-secrets');
        const includeReconstructed = document.getElementById('session-analyze-include-reconstructed');
        const continueOnError = document.getElementById('session-analyze-continue-on-error');
        const maxFiles = document.getElementById('session-analyze-max-files');
        const maxFailures = document.getElementById('session-analyze-max-failures');
        const timeoutMs = document.getElementById('session-analyze-timeout-ms');
        const retryAttempts = document.getElementById('session-analyze-retry-attempts');

        const defaults = this.getSessionAnalyzeDefaultOptions();
        const analysisType = String(typeSelect?.value || defaults.analysis_type).toLowerCase() === 'jsluice'
            ? 'jsluice'
            : 'comprehensive';
        const options = {
            run_mode: runMode === 'quick' ? 'quick' : 'advanced',
            analysis_type: analysisType,
            include_sourcemap: includeSourcemap ? includeSourcemap.checked : defaults.include_sourcemap,
            resolve_urls: resolveUrls ? resolveUrls.checked : defaults.resolve_urls,
            use_rep_endpoints: useRepEndpoints ? useRepEndpoints.checked : defaults.use_rep_endpoints,
            use_rep_secrets: useRepSecrets ? useRepSecrets.checked : defaults.use_rep_secrets,
            use_jsluice_endpoints: useJSluiceEndpoints ? useJSluiceEndpoints.checked : defaults.use_jsluice_endpoints,
            use_jsluice_secrets: useJSluiceSecrets ? useJSluiceSecrets.checked : defaults.use_jsluice_secrets,
            include_reconstructed_sources: includeReconstructed ? includeReconstructed.checked : defaults.include_reconstructed_sources,
            continue_on_error: continueOnError ? continueOnError.checked : defaults.continue_on_error,
            max_files_to_analyze: this.parseOptionalPositiveInt(maxFiles?.value, 1, 20000),
            max_failures: this.parseOptionalPositiveInt(maxFailures?.value, 1, 5000),
            per_file_timeout_ms: this.parseOptionalPositiveInt(timeoutMs?.value, 250, 120000),
            retry_attempts: this.parseOptionalPositiveInt(retryAttempts?.value, 1, 5) || 0,
        };

        if (analysisType === 'jsluice') {
            options.use_rep_endpoints = false;
            options.use_rep_secrets = false;
            options.use_jsluice_endpoints = true;
            options.use_jsluice_secrets = true;
        }
        return options;
    }

    getQuickSessionAnalyzeOptions() {
        const defaults = this.loadSessionAnalyzeDefaults();
        return {
            ...this.getSessionAnalyzeDefaultOptions(),
            ...defaults,
            run_mode: 'quick',
            analysis_type: 'comprehensive',
            use_rep_endpoints: true,
            use_rep_secrets: true,
            use_jsluice_endpoints: false,
            use_jsluice_secrets: false,
            max_files_to_analyze: null,
            max_failures: null,
            per_file_timeout_ms: null,
            retry_attempts: 0,
            continue_on_error: true,
        };
    }

    setSessionAnalyzeModalBusy(isBusy) {
        const quickBtn = document.getElementById('session-analyze-run-quick');
        const advancedBtn = document.getElementById('session-analyze-run-advanced');
        const closeButtons = document.querySelectorAll('#sessionAnalyzeConfigModal [data-bs-dismiss="modal"]');
        if (quickBtn) quickBtn.disabled = Boolean(isBusy);
        if (advancedBtn) advancedBtn.disabled = Boolean(isBusy);
        closeButtons.forEach((node) => {
            node.disabled = Boolean(isBusy);
        });
    }

    openSessionAnalyzeConfig(sessionId) {
        if (!sessionId) return;
        if (this.runningSessionAnalyses.has(sessionId)) {
            this.showAlert('Session analysis is already running for this session.', 'info');
            return;
        }

        const modalElement = document.getElementById('sessionAnalyzeConfigModal');
        if (!modalElement) {
            this.showAlert('Analyze-All config modal is unavailable.', 'danger');
            return;
        }

        this.activeSessionAnalyzeTargetId = sessionId;
        this.populateSessionAnalyzeModal(sessionId);
        if (!this.sessionAnalyzeModal) {
            this.sessionAnalyzeModal = new bootstrap.Modal(modalElement);
        }
        this.setSessionAnalyzeModalBusy(false);
        this.sessionAnalyzeModal.show();
    }

    async submitSessionAnalyzeFromModal(mode = 'advanced') {
        const sessionId = this.activeSessionAnalyzeTargetId;
        if (!sessionId) {
            this.showAlert('No session selected for analysis.', 'warning');
            return;
        }
        const options = mode === 'quick'
            ? this.getQuickSessionAnalyzeOptions()
            : this.collectSessionAnalyzeModalOptions('advanced');

        if (mode !== 'quick') {
            this.saveSessionAnalyzeDefaults(options);
        }
        this.setSessionAnalyzeModalBusy(true);

        try {
            await this.startSessionAnalysisWithOptions(sessionId, options);
            if (this.sessionAnalyzeModal) {
                this.sessionAnalyzeModal.hide();
            }
            this.activeSessionAnalyzeTargetId = null;
        } catch (_error) {
            // Keep modal open so user can adjust options and retry.
        } finally {
            this.setSessionAnalyzeModalBusy(false);
        }
    }

    async startSessionAnalysisWithOptions(sessionId, options = {}) {
        if (!sessionId) return;
        if (this.runningSessionAnalyses.has(sessionId)) {
            this.showAlert('Session analysis is already running.', 'info');
            return;
        }

        this.runningSessionAnalyses.add(sessionId);
        try {
            const response = await axios.post(
                `${this.apiBase}/api/sessions/${sessionId}/analyze/start`,
                { options }
            );
            const data = response.data || {};
            const job = data.job || null;

            if (job) {
                this.sessionAnalysisProgress.set(sessionId, job);
            }

            if (data.started) {
                this.sessionCompletionNotified.delete(sessionId);
                const mode = String(options?.run_mode || 'advanced').toLowerCase() === 'quick'
                    ? 'Quick run started.'
                    : 'Advanced run started.';
                this.showAlert(`Session analysis started. ${mode} Live progress is now visible.`, 'info');
            } else {
                this.showAlert('Session analysis is already running.', 'info');
            }

            this.startSessionProgressPolling(sessionId);
            await this.refreshViewsForSessionProgress(sessionId, {
                job: job || this.sessionAnalysisProgress.get(sessionId) || null,
                fullReload: false,
            });
        } catch (error) {
            this.runningSessionAnalyses.delete(sessionId);
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Session analysis failed: ${message}`, 'danger');
            throw error;
        }
    }

    getStoredFileDisplayName(fileId) {
        if (!fileId) return 'File';
        const row = document.querySelector(`#files-content [data-file-id="${fileId}"]`);
        const anchor = row?.querySelector('.result-header a');
        const url = (anchor?.textContent || '').trim();
        if (url) return url;
        return `File ${this.shortId(fileId)}`;
    }

    populateFileAnalyzeModal(fileId) {
        const targetLabel = document.getElementById('file-analyze-target-name');
        const typeSelect = document.getElementById('file-analyze-analysis-type');
        const includeSourcemap = document.getElementById('file-analyze-include-sourcemap');
        const resolveUrls = document.getElementById('file-analyze-resolve-urls');
        const useRepEndpoints = document.getElementById('file-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('file-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('file-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('file-analyze-use-jsluice-secrets');
        const includeReconstructed = document.getElementById('file-analyze-include-reconstructed');
        const retryAttempts = document.getElementById('file-analyze-retry-attempts');

        const defaults = this.loadSessionAnalyzeDefaults();
        if (targetLabel) targetLabel.textContent = this.getStoredFileDisplayName(fileId);
        if (typeSelect) typeSelect.value = defaults.analysis_type || 'comprehensive';
        if (includeSourcemap) includeSourcemap.checked = Boolean(defaults.include_sourcemap);
        if (resolveUrls) resolveUrls.checked = Boolean(defaults.resolve_urls);
        if (useRepEndpoints) useRepEndpoints.checked = Boolean(defaults.use_rep_endpoints);
        if (useRepSecrets) useRepSecrets.checked = Boolean(defaults.use_rep_secrets);
        if (useJSluiceEndpoints) useJSluiceEndpoints.checked = Boolean(defaults.use_jsluice_endpoints);
        if (useJSluiceSecrets) useJSluiceSecrets.checked = Boolean(defaults.use_jsluice_secrets);
        if (includeReconstructed) includeReconstructed.checked = Boolean(defaults.include_reconstructed_sources);
        if (retryAttempts) retryAttempts.value = Number(defaults.retry_attempts || 0);
        this.syncFileAnalyzeExtractorsWithType();
    }

    syncFileAnalyzeExtractorsWithType() {
        const typeSelect = document.getElementById('file-analyze-analysis-type');
        const useRepEndpoints = document.getElementById('file-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('file-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('file-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('file-analyze-use-jsluice-secrets');
        if (!typeSelect || !useRepEndpoints || !useRepSecrets || !useJSluiceEndpoints || !useJSluiceSecrets) {
            return;
        }

        const analysisType = String(typeSelect.value || 'comprehensive').toLowerCase();
        const lockForJsluice = analysisType === 'jsluice';
        useRepEndpoints.disabled = lockForJsluice;
        useRepSecrets.disabled = lockForJsluice;
        useJSluiceEndpoints.disabled = lockForJsluice;
        useJSluiceSecrets.disabled = lockForJsluice;

        if (lockForJsluice) {
            useRepEndpoints.checked = false;
            useRepSecrets.checked = false;
            useJSluiceEndpoints.checked = true;
            useJSluiceSecrets.checked = true;
        }
    }

    collectFileAnalyzeModalOptions(runMode = 'advanced') {
        const typeSelect = document.getElementById('file-analyze-analysis-type');
        const includeSourcemap = document.getElementById('file-analyze-include-sourcemap');
        const resolveUrls = document.getElementById('file-analyze-resolve-urls');
        const useRepEndpoints = document.getElementById('file-analyze-use-rep-endpoints');
        const useRepSecrets = document.getElementById('file-analyze-use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('file-analyze-use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('file-analyze-use-jsluice-secrets');
        const includeReconstructed = document.getElementById('file-analyze-include-reconstructed');
        const retryAttempts = document.getElementById('file-analyze-retry-attempts');

        const defaults = this.getSessionAnalyzeDefaultOptions();
        const analysisType = String(typeSelect?.value || defaults.analysis_type).toLowerCase() === 'jsluice'
            ? 'jsluice'
            : 'comprehensive';
        const options = {
            run_mode: runMode === 'quick' ? 'quick' : 'advanced',
            analysis_type: analysisType,
            include_sourcemap: includeSourcemap ? includeSourcemap.checked : defaults.include_sourcemap,
            resolve_urls: resolveUrls ? resolveUrls.checked : defaults.resolve_urls,
            use_rep_endpoints: useRepEndpoints ? useRepEndpoints.checked : defaults.use_rep_endpoints,
            use_rep_secrets: useRepSecrets ? useRepSecrets.checked : defaults.use_rep_secrets,
            use_jsluice_endpoints: useJSluiceEndpoints ? useJSluiceEndpoints.checked : defaults.use_jsluice_endpoints,
            use_jsluice_secrets: useJSluiceSecrets ? useJSluiceSecrets.checked : defaults.use_jsluice_secrets,
            include_reconstructed_sources: includeReconstructed ? includeReconstructed.checked : defaults.include_reconstructed_sources,
            retry_attempts: this.parseOptionalPositiveInt(retryAttempts?.value, 1, 5) || 0,
            continue_on_error: true,
            max_files_to_analyze: null,
            max_failures: null,
            per_file_timeout_ms: null,
        };

        if (analysisType === 'jsluice') {
            options.use_rep_endpoints = false;
            options.use_rep_secrets = false;
            options.use_jsluice_endpoints = true;
            options.use_jsluice_secrets = true;
        }
        return options;
    }

    getQuickFileAnalyzeOptions() {
        const defaults = this.loadSessionAnalyzeDefaults();
        return {
            ...this.getSessionAnalyzeDefaultOptions(),
            ...defaults,
            run_mode: 'quick',
            analysis_type: 'comprehensive',
            use_rep_endpoints: true,
            use_rep_secrets: true,
            use_jsluice_endpoints: false,
            use_jsluice_secrets: false,
            retry_attempts: 0,
            continue_on_error: true,
            max_files_to_analyze: null,
            max_failures: null,
            per_file_timeout_ms: null,
        };
    }

    setFileAnalyzeModalBusy(isBusy) {
        const quickBtn = document.getElementById('file-analyze-run-quick');
        const advancedBtn = document.getElementById('file-analyze-run-advanced');
        const closeButtons = document.querySelectorAll('#fileAnalyzeConfigModal [data-bs-dismiss="modal"]');
        if (quickBtn) quickBtn.disabled = Boolean(isBusy);
        if (advancedBtn) advancedBtn.disabled = Boolean(isBusy);
        closeButtons.forEach((node) => {
            node.disabled = Boolean(isBusy);
        });
    }

    setCreateSessionModalBusy(isBusy) {
        const submitBtn = document.getElementById('create-session-submit-btn');
        const closeButtons = document.querySelectorAll('#createSessionModal [data-bs-dismiss="modal"]');
        if (submitBtn) {
            submitBtn.disabled = Boolean(isBusy);
            submitBtn.innerHTML = isBusy
                ? '<i class="fas fa-spinner fa-spin me-1"></i>Starting...'
                : '<i class="fas fa-play me-1"></i>Start Session Crawl';
        }
        closeButtons.forEach((node) => {
            node.disabled = Boolean(isBusy);
        });
    }

    openCreateSessionModal() {
        const modalElement = document.getElementById('createSessionModal');
        if (!modalElement) {
            this.showAlert('Create session modal is unavailable.', 'danger');
            return;
        }

        const targetInput = document.getElementById('create-session-target-url');
        if (targetInput && !targetInput.value.trim()) {
            targetInput.value = 'https://wishandwash.co.il';
        }

        if (!this.createSessionModal) {
            this.createSessionModal = new bootstrap.Modal(modalElement);
        }
        this.setCreateSessionModalBusy(false);
        this.createSessionModal.show();
    }

    collectCreateSessionPayload() {
        const sessionNameInput = document.getElementById('create-session-name');
        const targetUrlInput = document.getElementById('create-session-target-url');
        const discoveryEngineSelect = document.getElementById('create-session-discovery-engine');
        const maxAssetsInput = document.getElementById('create-session-max-assets');
        const maxDepthInput = document.getElementById('create-session-max-depth');
        const timeoutInput = document.getElementById('create-session-timeout-seconds');
        const waitInput = document.getElementById('create-session-wait-after-load-ms');
        const maxResponseInput = document.getElementById('create-session-max-response-bytes');
        const sameOriginInput = document.getElementById('create-session-same-origin');
        const includeSourcemapsInput = document.getElementById('create-session-include-sourcemaps');
        const performAnalysisInput = document.getElementById('create-session-perform-analysis');

        const targetUrl = (targetUrlInput?.value || '').trim();
        const sessionName = (sessionNameInput?.value || '').trim();
        const discoveryEngine = (discoveryEngineSelect?.value || 'katana').trim().toLowerCase();
        const sessionId = this.generateClientUuid();

        const payload = {
            sessionId,
            url: targetUrl,
            discoveryEngine,
            sameOriginOnly: sameOriginInput ? sameOriginInput.checked : true,
            includeSourceMaps: includeSourcemapsInput ? includeSourcemapsInput.checked : true,
            performAnalysis: performAnalysisInput ? performAnalysisInput.checked : true,
            maxAssets: this.parseOptionalPositiveInt(maxAssetsInput?.value, 1, 5000) || 500,
            maxDepth: this.parseOptionalInt(maxDepthInput?.value, 0, 5) ?? 3,
            timeoutSeconds: this.parseOptionalPositiveInt(timeoutInput?.value, 3, 120) || 20,
            waitAfterLoadMs: this.parseOptionalInt(waitInput?.value, 0, 30000) ?? 2500,
            maxResponseBytes: this.parseOptionalPositiveInt(maxResponseInput?.value, 1024, 50 * 1024 * 1024) || (12 * 1024 * 1024),
        };
        if (sessionName) {
            payload.sessionName = sessionName;
        }
        return payload;
    }

    async submitCreateSessionFromModal() {
        const payload = this.collectCreateSessionPayload();
        if (!payload.url) {
            this.showAlert('Please provide a target URL.', 'warning');
            return;
        }

        this.setCreateSessionModalBusy(true);
        try {
            const response = await axios.post(`${this.apiBase}/api/recon/jobs/start`, payload);
            const data = response.data || {};
            const jobId = data.jobId;
            const sessionId = data.sessionId || payload.sessionId;
            const job = data.job || null;
            const sessionCreated = Boolean(data.sessionCreated);
            if (job && sessionId) {
                this.reconSessionProgress.set(sessionId, job);
            }
            if (this.createSessionModal) {
                this.createSessionModal.hide();
            }
            this.switchTab('sessions');
            await this.loadSessions();
            if (jobId) {
                this.startReconJobPolling(jobId, sessionId);
            }
            this.showAlert(
                sessionCreated
                    ? `Session created and crawl started (${this.shortId(sessionId)}).`
                    : `Session crawl started (${this.shortId(sessionId)}).`,
                'success'
            );
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Failed to start session crawl: ${message}`, 'danger');
        } finally {
            this.setCreateSessionModalBusy(false);
        }
    }

    startReconJobPolling(jobId, sessionId) {
        if (!jobId) return;
        if (this.reconJobPollers.has(jobId)) return;

        const tick = async () => {
            if (this.reconPollingInFlight.has(jobId)) return;
            this.reconPollingInFlight.add(jobId);
            try {
                const response = await axios.get(`${this.apiBase}/api/recon/jobs/${jobId}`);
                const job = response?.data?.job || null;
                if (!job) {
                    this.stopReconJobPolling(jobId);
                    return;
                }

                const effectiveSessionId = sessionId || job.sessionId;
                if (effectiveSessionId) {
                    this.reconSessionProgress.set(effectiveSessionId, job);
                }

                const status = String(job.status || '').toLowerCase();
                if (this.activeTab === 'sessions') {
                    const patched = effectiveSessionId
                        ? this.patchSessionReconProgressRow(effectiveSessionId, job)
                        : false;
                    if (!patched) {
                        await this.loadSessions();
                    }
                } else {
                    // Avoid full-page churn while polling; refresh counters on terminal states.
                }

                if (['completed', 'failed', 'cancelled'].includes(status)) {
                    this.stopReconJobPolling(jobId);
                    if (effectiveSessionId) {
                        this.reconSessionProgress.delete(effectiveSessionId);
                    }
                    await this.loadStatistics();
                    if (this.activeTab === 'sessions') {
                        await this.loadSessions();
                    }
                    if (status === 'completed') {
                        this.showAlert(`Crawl completed for session ${this.shortId(effectiveSessionId || sessionId)}.`, 'success');
                    } else if (status === 'cancelled') {
                        this.showAlert(`Crawl stopped for session ${this.shortId(effectiveSessionId || sessionId)}.`, 'warning');
                    } else {
                        const reason = job.error ? ` ${job.error}` : '';
                        this.showAlert(`Crawl failed for session ${this.shortId(effectiveSessionId || sessionId)}.${reason}`, 'danger');
                    }
                }
            } catch (error) {
                this.stopReconJobPolling(jobId);
                const detail = error?.response?.data?.detail;
                const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
                this.showAlert(`Recon polling failed: ${message}`, 'warning');
            } finally {
                this.reconPollingInFlight.delete(jobId);
            }
        };

        tick();
        const intervalId = window.setInterval(tick, 2000);
        this.reconJobPollers.set(jobId, intervalId);
    }

    stopReconJobPolling(jobId) {
        const intervalId = this.reconJobPollers.get(jobId);
        if (!intervalId) return;
        window.clearInterval(intervalId);
        this.reconJobPollers.delete(jobId);
        this.reconPollingInFlight.delete(jobId);
    }

    stopReconPollingForSession(sessionId) {
        if (!sessionId) return;
        const reconState = this.reconSessionProgress.get(sessionId) || null;
        const jobId = reconState?.jobId;
        if (jobId) {
            this.stopReconJobPolling(jobId);
        }
        this.reconSessionProgress.delete(sessionId);
    }

    openFileAnalyzeConfig(fileId) {
        if (!fileId) return;
        if (this.runningFileAnalyses.has(fileId)) {
            this.showAlert('File analysis is already running.', 'info');
            return;
        }

        const modalElement = document.getElementById('fileAnalyzeConfigModal');
        if (!modalElement) {
            this.showAlert('File analysis config modal is unavailable.', 'danger');
            return;
        }

        this.activeFileAnalyzeTargetId = fileId;
        this.populateFileAnalyzeModal(fileId);
        if (!this.fileAnalyzeModal) {
            this.fileAnalyzeModal = new bootstrap.Modal(modalElement);
        }
        this.setFileAnalyzeModalBusy(false);
        this.fileAnalyzeModal.show();
    }

    async submitFileAnalyzeFromModal(mode = 'advanced') {
        const fileId = this.activeFileAnalyzeTargetId;
        if (!fileId) {
            this.showAlert('No file selected for analysis.', 'warning');
            return;
        }
        const options = mode === 'quick'
            ? this.getQuickFileAnalyzeOptions()
            : this.collectFileAnalyzeModalOptions('advanced');

        if (mode !== 'quick') {
            this.saveSessionAnalyzeDefaults(options);
        }
        this.setFileAnalyzeModalBusy(true);

        try {
            await this.startStoredFileAnalysisWithOptions(fileId, options);
            if (this.fileAnalyzeModal) {
                this.fileAnalyzeModal.hide();
            }
            this.activeFileAnalyzeTargetId = null;
        } catch (_error) {
            // Keep modal open so user can adjust options and retry.
        } finally {
            this.setFileAnalyzeModalBusy(false);
        }
    }

    isFilesFilterActive() {
        return Boolean((this.filesFilter.query || '').trim()) || (this.filesFilter.status && this.filesFilter.status !== 'all');
    }

    applyFileFilters(files) {
        const query = (this.filesFilter.query || '').trim().toLowerCase();
        const statusFilter = (this.filesFilter.status || 'all').toLowerCase();

        return (files || []).filter((file) => {
            const effectiveStatus = this.runningFileAnalyses.has(file.id)
                ? 'analyzing'
                : String(file.analysisStatus || 'not_analyzed').toLowerCase();

            if (statusFilter !== 'all' && effectiveStatus !== statusFilter) {
                return false;
            }

            if (!query) return true;
            const haystack = `${file.url || ''} ${file.contentHash || ''} ${file.sessionId || ''}`.toLowerCase();
            return haystack.includes(query);
        });
    }

    isSessionsFilterActive() {
        return Boolean((this.sessionsFilter.query || '').trim()) || (this.sessionsFilter.status && this.sessionsFilter.status !== 'all');
    }

    getSessionFilterStatus(sessionId) {
        if (!sessionId) return 'idle';
        if (this.runningSessionAnalyses.has(sessionId)) return 'active';

        const progressState = this.sessionAnalysisProgress.get(sessionId) || null;
        const status = String(progressState?.jobStatus || '').toLowerCase();
        if (['queued', 'running', 'cancelling'].includes(status)) return 'active';
        if (['completed', 'failed', 'cancelled'].includes(status)) return status;

        const reconState = this.reconSessionProgress.get(sessionId) || null;
        const reconStatus = String(reconState?.status || '').toLowerCase();
        if (['queued', 'running', 'cancelling'].includes(reconStatus)) return 'active';
        if (['completed', 'failed', 'cancelled'].includes(reconStatus)) return reconStatus;
        return 'idle';
    }

    applySessionFilters(sessions) {
        const query = (this.sessionsFilter.query || '').trim().toLowerCase();
        const statusFilter = (this.sessionsFilter.status || 'all').toLowerCase();

        return (sessions || []).filter((session) => {
            const sessionStatus = this.getSessionFilterStatus(session.id);
            if (statusFilter !== 'all' && sessionStatus !== statusFilter) {
                return false;
            }

            if (!query) return true;
            const displayName = (session.name && session.name.trim()) ? session.name.trim() : `Session ${this.shortId(session.id)}`;
            const haystack = `${displayName} ${session.id || ''}`.toLowerCase();
            return haystack.includes(query);
        });
    }

    getSessionDisplayName(sessionId) {
        if (!sessionId) return 'Session';

        const labelNode = document.getElementById(`session-name-display-${sessionId}`);
        if (labelNode) {
            const currentName = (labelNode.dataset.currentName || labelNode.textContent || '').trim();
            if (currentName) {
                return currentName;
            }
        }

        if (this.activeFilesSessionId === sessionId && this.activeFilesSessionName) {
            return this.activeFilesSessionName;
        }

        return `Session ${this.shortId(sessionId)}`;
    }

    setupDragAndDrop() {
        const textarea = document.getElementById('js-content');
        
        textarea.addEventListener('dragover', (e) => {
            e.preventDefault();
            textarea.classList.add('drag-over');
        });

        textarea.addEventListener('dragleave', () => {
            textarea.classList.remove('drag-over');
        });

        textarea.addEventListener('drop', (e) => {
            e.preventDefault();
            textarea.classList.remove('drag-over');
            
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                const file = files[0];
                if (file.type === 'application/javascript' || file.name.endsWith('.js')) {
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        textarea.value = e.target.result;
                        this.validateInput();
                    };
                    reader.readAsText(file);
                } else {
                    this.showAlert('Please select a JavaScript (.js) file', 'warning');
                }
            }
        });
    }

    updateApiStatusIndicator(connected) {
        const dot  = document.getElementById('api-status-dot');
        const text = document.getElementById('api-status-text');
        if (dot)  dot.className  = connected ? 'dot dot-live' : 'dot dot-error';
        if (text) text.textContent = connected ? 'API Connected' : 'API Disconnected';
    }

    async checkAPIStatus() {
        try {
            const [healthResponse, sessionsResponse] = await Promise.all([
                axios.get(`${this.apiBase}/health`),
                axios.get(`${this.apiBase}/api/sessions`)
            ]);
            const sessionsOk = Array.isArray(sessionsResponse.data);
            if (healthResponse.data.status === 'healthy' && sessionsOk) {
                this.updateApiStatusIndicator(true);
            } else {
                throw new Error('Unhealthy response');
            }
        } catch (error) {
            this.updateApiStatusIndicator(false);
            console.error('API Status Error:', error);
        }
    }

    async loadStatistics() {
        try {
            const response = await axios.get(`${this.apiBase}/api/sessions`);
            const sessions = Array.isArray(response.data) ? response.data : [];

            const totalSessions = sessions.length;
            const totalFiles = sessions.reduce((sum, session) => {
                const fileCount = Number(session.fileCount) || 0;
                return sum + fileCount;
            }, 0);

            document.getElementById('total-files').textContent = String(totalFiles);
            document.getElementById('total-sessions').textContent = String(totalSessions);
            document.getElementById('total-endpoints').textContent = '-';
        } catch (error) {
            console.error('Statistics Error:', error);
            document.getElementById('total-files').textContent = '-';
            document.getElementById('total-sessions').textContent = '-';
            document.getElementById('total-endpoints').textContent = '-';
        }
    }

    validateInput() {
        const content = document.getElementById('js-content').value;
        const url = document.getElementById('js-url').value;
        
        const submitBtn = document.querySelector('#analysis-form button[type="submit"]');
        
        if (url.trim() && content.trim()) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-play"></i> Start Analysis';
        } else if (url.trim()) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fas fa-download"></i> Fetch URL and Analyze';
        } else {
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i class="fas fa-play"></i> Enter Source URL';
        }
    }

    async performAnalysis() {
        const content = document.getElementById('js-content').value.trim();
        const url = document.getElementById('js-url').value.trim();
        const analysisType = document.querySelector('input[name="analysis-type"]:checked').value;

        if (!url) {
            this.showAlert('Please provide a JavaScript URL', 'warning');
            return;
        }

        this.showLoadingModal(true);

        try {
            const startTime = Date.now();
            const basePayload = {
                url: url,
                metadata: {
                    source: 'dashboard',
                    timestamp: new Date().toISOString()
                },
                options: this.collectAnalysisOptions()
            };
            const hasManualContent = Boolean(content);
            const endpoint = hasManualContent
                ? (analysisType === 'comprehensive' ? '/api/analyze-comprehensive' : '/api/analyze-jsluice')
                : '/api/analyze-by-url';
            const payload = hasManualContent
                ? { ...basePayload, content }
                : { ...basePayload, analysis_type: analysisType };

            const response = await axios.post(`${this.apiBase}${endpoint}`, payload);
            const processingTime = Date.now() - startTime;

            this.currentResults = response.data;
            this.displayResults(response.data, response.data.processing_time_ms || processingTime, {
                sourceUrl: url,
                fileId: null,
                sessionId: null,
                sourceMapStatus: null,
                sourceMapPreview: null,
                sourceMapUrl: null,
                analysisStatus: response.data?.status || 'completed',
                analysisUpdatedAt: new Date().toISOString(),
                origin: hasManualContent ? 'manual_content' : 'manual_fetch',
                sourceContentLoaded: hasManualContent,
            });
            this.showAlert(hasManualContent ? 'Analysis completed successfully!' : 'URL fetched and analyzed successfully!', 'success');

        } catch (error) {
            console.error('Analysis Error:', error);
            
            let errorMessage = 'Analysis failed. Please check your input and try again.';
            if (error.response && error.response.data && error.response.data.detail) {
                if (typeof error.response.data.detail === 'string') {
                    errorMessage = error.response.data.detail;
                } else if (error.response.data.detail.message) {
                    errorMessage = error.response.data.detail.message;
                }
            }
            
            this.showAlert(errorMessage, 'danger');
        } finally {
            this.showLoadingModal(false);
        }
    }

    displayResults(results, processingTime, context = null) {
        // Show results section
        document.getElementById('results-section').style.display = 'block';
        document.getElementById('processing-time').textContent = `${processingTime}ms`;
        this.resultsContext = context || this.buildResultsContextFromResults(results);
        this.updateResultsContextUI();
        this.updateAnalysisContextCard(this.resultsContext);

        // Get the analysis data (handle both comprehensive and jsluice response formats)
        let analysis;
        if (results.analysis && results.analysis.analysis) {
            // Comprehensive analysis format
            analysis = results.analysis.analysis;
        } else if (results.analysis) {
            // JSluice format
            analysis = results.analysis;
        } else {
            analysis = results;
        }

        // Display endpoints
        this.displayEndpoints(analysis.endpoints || analysis.urls || []);
        
        // Display secrets
        this.displaySecrets(analysis.secrets || []);
        
        // Display dependencies
        this.displayDependencies(analysis.dependencies || []);
        
        // Display source map info
        this.displaySourceMap(
            analysis.sourcemap || analysis.reconstructed_files || [],
            this.resultsContext?.sourceMapPreview || null
        );

        // Scroll to results
        document.getElementById('results-section').scrollIntoView({ behavior: 'smooth' });
    }

    populateAnalysisInputs(url, content) {
        const urlInput = document.getElementById('js-url');
        const contentInput = document.getElementById('js-content');
        if (urlInput) {
            urlInput.value = url || '';
        }
        if (contentInput) {
            contentInput.value = content || '';
        }
        this.validateInput();
    }

    applyAnalysisOptionsToForm(options = {}) {
        const source = options && typeof options === 'object' ? options : {};
        const getOption = (keys, fallback = null) => {
            for (const key of keys) {
                if (Object.prototype.hasOwnProperty.call(source, key)) {
                    return source[key];
                }
            }
            return fallback;
        };

        const selectedType = String(
            getOption(['analysis_type', 'analysisType'], 'comprehensive')
        ).toLowerCase() === 'jsluice'
            ? 'jsluice'
            : 'comprehensive';
        const comprehensiveRadio = document.getElementById('comprehensive');
        const jsluiceRadio = document.getElementById('jsluice');
        if (selectedType === 'jsluice') {
            if (jsluiceRadio) jsluiceRadio.checked = true;
        } else if (comprehensiveRadio) {
            comprehensiveRadio.checked = true;
        }

        const checkboxBindings = [
            { id: 'include-sourcemap', keys: ['include_sourcemap', 'includeSourcemap', 'includeSourceMap'] },
            { id: 'resolve-urls', keys: ['resolve_urls', 'resolveUrls'] },
            { id: 'use-rep-endpoints', keys: ['use_rep_endpoints', 'useRepEndpoints'] },
            { id: 'use-rep-secrets', keys: ['use_rep_secrets', 'useRepSecrets'] },
            { id: 'use-jsluice-endpoints', keys: ['use_jsluice_endpoints', 'useJsluiceEndpoints'] },
            { id: 'use-jsluice-secrets', keys: ['use_jsluice_secrets', 'useJsluiceSecrets'] },
        ];

        checkboxBindings.forEach(({ id, keys }) => {
            const input = document.getElementById(id);
            if (!input) return;
            const value = getOption(keys, undefined);
            if (value !== undefined) {
                input.checked = Boolean(value);
            }
        });

        this.validateInput();
    }

    normalizeStoredFileMeta(fileMeta, analysisData = {}) {
        const metadata = analysisData?.metadata || {};
        return {
            ...fileMeta,
            url: fileMeta?.url || fileMeta?.sourceUrl || metadata?.url || '',
            sessionId: fileMeta?.sessionId || fileMeta?.session_id || analysisData?.sessionId || null,
            sourceMap: fileMeta?.sourceMap || fileMeta?.source_map || null,
        };
    }

    buildResultsContext(fileMeta, fileId, sourceMapPayload, options = {}) {
        const { analysisData = null, sourceContentLoaded = false } = options;
        const sourceMap = sourceMapPayload?.sourceMap || fileMeta?.sourceMap || null;
        const hasMapContent = Boolean(sourceMapPayload?.content);
        return {
            sourceUrl: fileMeta?.url || null,
            fileId: fileId || fileMeta?.id || null,
            sessionId: fileMeta?.sessionId || null,
            sourceMapStatus: sourceMap?.processingStatus || (hasMapContent ? 'available' : 'none'),
            sourceMapUrl: sourceMap?.detectedMapUrl || sourceMap?.mapUrl || null,
            sourceMapPreview: hasMapContent
                ? {
                    path: sourceMap?.detectedMapUrl || sourceMap?.mapUrl || 'stored source map',
                    content: sourceMapPayload.content,
                    size: sourceMapPayload.content.length,
                }
                : null,
            analysisStatus: analysisData?.status || null,
            analysisError: analysisData?.error || null,
            analysisUpdatedAt: analysisData?.updatedAt || null,
            failureSource: this.deriveFailureInfo({
                analysisStatus: analysisData?.status,
                analysisError: analysisData?.error,
                sourceMap,
            })?.source || null,
            origin: 'stored_file',
            sourceContentLoaded,
        };
    }

    buildResultsContextFromResults(results) {
        const metadata = results?.metadata || {};
        return {
            sourceUrl: metadata.url || null,
            fileId: results?.fileId || null,
            sessionId: results?.sessionId || null,
            sourceMapStatus: null,
            sourceMapUrl: metadata.sourceMapUrl || null,
            sourceMapPreview: null,
            analysisStatus: results?.status || null,
            analysisError: results?.error || null,
            analysisUpdatedAt: results?.updatedAt || null,
            failureSource: this.deriveFailureInfo({
                analysisStatus: results?.status,
                analysisError: results?.error,
                sourceMap: null,
            })?.source || null,
            origin: results?.fileId ? 'stored_file' : 'manual',
            sourceContentLoaded: false,
        };
    }

    updateResultsContextUI() {
        const node = document.getElementById('results-context');
        if (!node) return;
        const context = this.resultsContext || {};
        if (!context.sourceUrl && !context.fileId) {
            node.textContent = 'No analysis context selected.';
            node.title = '';
            return;
        }

        const modeLabel = context.origin === 'stored_file' ? 'Stored file analysis' : 'Ad hoc analysis';
        const parts = [modeLabel];
        if (context.sourceUrl) {
            parts.push(`Source: ${context.sourceUrl}`);
        }
        if (context.fileId) {
            parts.push(`File ID: ${this.shortId(context.fileId)}`);
        }
        if (context.sessionId) {
            parts.push(`Session: ${this.shortId(context.sessionId)}`);
        }
        if (context.analysisStatus) {
            parts.push(`Status: ${context.analysisStatus}`);
        }
        if (context.analysisStatus === 'failed' && context.failureSource) {
            parts.push(`Failure: ${context.failureSource}`);
        }
        if (context.sourceMapStatus) {
            parts.push(`Map: ${context.sourceMapStatus}`);
        }
        node.textContent = parts.join(' | ');
        node.title = context.sourceUrl || '';
    }

    updateAnalysisContextCard(context) {
        const card = document.getElementById('analysis-context-card');
        const backButton = document.getElementById('analysis-context-back-btn');
        const modeNode = document.getElementById('analysis-context-mode');
        const urlNode = document.getElementById('analysis-context-url');
        const fileIdNode = document.getElementById('analysis-context-file-id');
        const sessionIdNode = document.getElementById('analysis-context-session-id');
        const statusNode = document.getElementById('analysis-context-status');
        const mapNode = document.getElementById('analysis-context-map');
        const failureNode = document.getElementById('analysis-context-failure');
        const contentNode = document.getElementById('analysis-context-content');

        if (!card || !backButton || !modeNode || !urlNode || !fileIdNode || !sessionIdNode || !statusNode || !mapNode || !failureNode || !contentNode) {
            return;
        }

        if (!context || (!context.sourceUrl && !context.fileId)) {
            card.classList.add('d-none');
            backButton.classList.add('d-none');
            return;
        }

        card.classList.remove('d-none');
        backButton.classList.toggle('d-none', !context.sessionId);
        modeNode.textContent = context.origin === 'stored_file' ? 'Stored file analysis' : 'Ad hoc analysis';

        if (context.sourceUrl) {
            urlNode.textContent = context.sourceUrl;
            const safeHref = this.safeExternalHref(context.sourceUrl);
            if (safeHref) {
                urlNode.href = safeHref;
                urlNode.setAttribute('target', '_blank');
                urlNode.setAttribute('rel', 'noopener noreferrer');
            } else {
                urlNode.removeAttribute('href');
                urlNode.removeAttribute('target');
                urlNode.removeAttribute('rel');
            }
        } else {
            urlNode.textContent = 'Unavailable';
            urlNode.removeAttribute('href');
            urlNode.removeAttribute('target');
            urlNode.removeAttribute('rel');
        }

        fileIdNode.textContent = context.fileId || '-';
        sessionIdNode.textContent = context.sessionId || '-';
        statusNode.textContent = context.analysisStatus || '-';

        const mapParts = [];
        if (context.sourceMapStatus) mapParts.push(context.sourceMapStatus);
        if (context.sourceMapUrl) mapParts.push(context.sourceMapUrl);
        mapNode.textContent = mapParts.length > 0 ? mapParts.join(' | ') : '-';

        const failureInfo = this.deriveFailureInfo({
            analysisStatus: context.analysisStatus,
            analysisError: context.analysisError,
            sourceMap: {
                processingStatus: context.sourceMapStatus,
            },
        });
        if (failureInfo) {
            failureNode.textContent = `${failureInfo.label}: ${failureInfo.details}`;
            failureNode.title = failureInfo.guidance;
        } else {
            failureNode.textContent = '-';
            failureNode.title = '';
        }

        if (context.origin === 'stored_file') {
            contentNode.textContent = context.sourceContentLoaded ? 'Loaded from storage' : 'Not available';
        } else {
            contentNode.textContent = context.sourceContentLoaded ? 'Provided manually' : 'Fetched by API';
        }
    }

    openAnalysisContextSession() {
        const sessionId = this.resultsContext?.sessionId || null;
        if (!sessionId) {
            this.showAlert('No session context available for this analysis.', 'warning');
            return;
        }
        this.openSessionFiles(sessionId, '');
    }

    async fetchStoredFileContent(fileId) {
        try {
            const response = await fetch(`${this.apiBase}/api/files/${fileId}/content`, {
                method: 'GET',
                credentials: 'same-origin',
            });
            if (!response.ok) {
                let detailMessage = null;
                const contentType = response.headers.get('content-type') || '';
                if (contentType.includes('application/json')) {
                    try {
                        const payload = await response.json();
                        const detail = payload?.detail;
                        if (typeof detail === 'string') {
                            detailMessage = detail;
                        } else if (detail && typeof detail.message === 'string') {
                            detailMessage = detail.message;
                        }
                    } catch (_error) {
                        detailMessage = null;
                    }
                }
                const statusText = detailMessage ? `HTTP ${response.status} (${detailMessage})` : `HTTP ${response.status}`;
                return { content: '', loaded: false, error: statusText };
            }
            const text = await response.text();
            if (typeof text !== 'string') {
                return { content: '', loaded: false, error: 'Unexpected response type' };
            }
            return { content: text, loaded: true, error: null };
        } catch (error) {
            return { content: '', loaded: false, error: error?.message || 'Unknown fetch error' };
        }
    }

    safeExternalHref(value) {
        if (!value) return null;
        try {
            const parsed = new URL(value, window.location.origin);
            if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
                return parsed.href;
            }
            return null;
        } catch (error) {
            return null;
        }
    }

    deriveFailureInfo(file) {
        if (this.failureUtils && typeof this.failureUtils.deriveFileFailure === 'function') {
            return this.failureUtils.deriveFileFailure(file);
        }
        const status = (file?.analysisStatus || '').toLowerCase();
        if (status !== 'failed') return null;
        const details = (file?.analysisError || '').trim() || 'No detailed error message was stored.';
        return {
            source: 'analysis',
            label: 'Analysis',
            details,
            guidance: 'Retry analysis after verifying extractor options and backend analyzer health.',
        };
    }

    renderFailurePanel(failureInfo) {
        if (!failureInfo) return '';
        return `
            <div class="failure-panel mt-2">
                <div class="failure-panel-title">
                    <i class="fas fa-triangle-exclamation me-1"></i>
                    ${this.escapeHtml(failureInfo.label)} failure
                </div>
                <div class="failure-panel-details">${this.escapeHtml(failureInfo.details)}</div>
                <div class="failure-panel-guidance"><strong>Next step:</strong> ${this.escapeHtml(failureInfo.guidance)}</div>
            </div>
        `;
    }

    displayEndpoints(endpoints) {
        const container = document.getElementById('endpoints-content');
        const count = document.getElementById('endpoints-count');
        
        count.textContent = endpoints.length;
        
        if (endpoints.length === 0) {
            container.innerHTML = this.getEmptyState('No endpoints found', 'globe');
            return;
        }

        container.innerHTML = endpoints.map(endpoint => {
            const confidenceClass = `confidence-${(endpoint.confidence || 'medium').toLowerCase()}`;
            const locationLabel = this.renderLocation(endpoint);
            const extractorBadges = this.renderExtractorBadges(endpoint);

            return `
                <div class="result-item">
                    <div class="result-header">
                        <div>
                            <h6 class="mb-1">
                                <i class="fas fa-link me-2"></i>
                                <span class="result-url">${this.escapeHtml(endpoint.url || endpoint.endpoint || 'Unknown URL')}</span>
                            </h6>
                            <div class="mb-1">
                                <span class="badge ${confidenceClass} confidence-badge me-2">${endpoint.confidence || 'medium'}</span>
                                <span class="badge bg-secondary me-2">${this.escapeHtml(endpoint.type || 'unknown')}</span>
                                ${extractorBadges}
                            </div>
                            ${locationLabel ? `<div class="text-muted"><small><i class="fas fa-location-dot me-1"></i>${this.escapeHtml(locationLabel)}</small></div>` : ''}
                            ${endpoint.occurrenceCount ? `<div class="text-muted"><small>${endpoint.occurrenceCount} occurrence(s)</small></div>` : ''}
                        </div>
                    </div>
                    ${endpoint.context ? `
                        <div class="result-context">
                            <strong>Context:</strong><br>
                            ${this.escapeHtml(endpoint.context)}
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    }

    displaySecrets(secrets) {
        const container = document.getElementById('secrets-content');
        const count = document.getElementById('secrets-count');
        
        count.textContent = secrets.length;
        
        if (secrets.length === 0) {
            container.innerHTML = this.getEmptyState('No secrets found', 'key');
            return;
        }

        container.innerHTML = secrets.map((secret, index) => {
            const confidenceClass = `confidence-${(secret.confidence || 'medium').toLowerCase()}`;
            const maskedValue = '*'.repeat(Math.min(secret.value?.length || 20, 20));
            const locationLabel = this.renderLocation(secret);
            const extractorBadges = this.renderExtractorBadges(secret);
            
            return `
                <div class="result-item">
                    <div class="result-header">
                        <div>
                            <h6 class="mb-2">
                                <i class="fas fa-exclamation-triangle text-warning me-2"></i>
                                ${secret.type || secret.rule || 'Secret'} Found
                            </h6>
                            <div class="mb-1">
                                <span class="badge ${confidenceClass} confidence-badge me-2">${secret.confidence || 'medium'}</span>
                                <span class="badge bg-secondary me-2">${this.escapeHtml(secret.type || 'secret')}</span>
                                ${extractorBadges}
                            </div>
                            ${locationLabel ? `<div class="text-muted"><small><i class="fas fa-location-dot me-1"></i>${this.escapeHtml(locationLabel)}</small></div>` : ''}
                            ${secret.occurrenceCount ? `<div class="text-muted"><small>${secret.occurrenceCount} occurrence(s)</small></div>` : ''}
                        </div>
                    </div>
                    <div class="secret-value masked" id="secret-${index}">
                        <span class="secret-text">${maskedValue}</span>
                        <button class="secret-toggle" onclick="dashboard.toggleSecret(${index}, '${this.escapeHtml(secret.value || secret.match || '')}')">
                            <i class="fas fa-eye"></i>
                        </button>
                    </div>
                    ${secret.context ? `
                        <div class="result-context">
                            <strong>Context:</strong><br>
                            ${this.escapeHtml(secret.context)}
                        </div>
                    ` : ''}
                </div>
            `;
        }).join('');
    }

    displayDependencies(dependencies) {
        const container = document.getElementById('dependencies-content');
        const count = document.getElementById('deps-count');
        
        count.textContent = dependencies.length;
        
        if (dependencies.length === 0) {
            container.innerHTML = this.getEmptyState('No dependencies found', 'sitemap');
            return;
        }

        container.innerHTML = dependencies.map(dep => `
            <div class="result-item">
                <div class="dependency-item">
                    <i class="fas fa-cube me-2"></i>
                    <strong>${this.escapeHtml(dep.name || dep.url || dep.dep_url || 'Unknown')}</strong>
                    ${dep.version ? `<span class="badge bg-info ms-2">v${dep.version}</span>` : ''}
                    ${dep.type ? `<span class="badge bg-secondary ms-2">${dep.type}</span>` : ''}
                    ${dep.resolvedUrl || dep.resolved_url ? `
                        <div class="mt-2 text-muted">
                            <small><i class="fas fa-arrow-right me-1"></i>${dep.resolvedUrl || dep.resolved_url}</small>
                        </div>
                    ` : ''}
                </div>
            </div>
        `).join('');
    }

    displaySourceMap(sourcemap, fallbackPreview = null) {
        const container = document.getElementById('sourcemap-content');
        const count = document.getElementById('sourcemap-count');
        
        let fileCount = 0;
        let content = '';
        
        if (sourcemap && typeof sourcemap === 'object') {
            if (sourcemap.files && sourcemap.files.length > 0) {
                fileCount = sourcemap.files.length;
                content = this.renderSourceMapFiles(sourcemap.files);
            } else if (Array.isArray(sourcemap) && sourcemap.length > 0) {
                fileCount = sourcemap.length;
                content = this.renderSourceMapFiles(sourcemap);
            } else if (sourcemap.success === false) {
                content = `
                    <div class="alert alert-warning">
                        <i class="fas fa-exclamation-triangle me-2"></i>
                        Source map processing failed: ${sourcemap.error || 'Unknown error'}
                    </div>
                `;
            }
        }

        if (fileCount === 0 && !content && fallbackPreview) {
            fileCount = 1;
            content = this.renderSourceMapFiles([fallbackPreview]);
        }
        
        count.textContent = fileCount;
        
        if (fileCount === 0 && !content) {
            container.innerHTML = this.getEmptyState('No source maps found', 'map');
        } else {
            container.innerHTML = content;
        }
    }

    renderSourceMapFiles(files) {
        return files.map((file, index) => `
            <div class="sourcemap-file">
                <div class="sourcemap-file-header" onclick="dashboard.toggleSourceMapFile(${index})">
                    <div>
                        <i class="fas fa-file-code me-2"></i>
                        <strong>${this.escapeHtml(file.path || file.name || `File ${index + 1}`)}</strong>
                        ${file.size ? `<span class="badge bg-secondary ms-2">${this.formatFileSize(file.size)}</span>` : ''}
                    </div>
                    <i class="fas fa-chevron-down" id="sourcemap-chevron-${index}"></i>
                </div>
                <div class="sourcemap-file-content" id="sourcemap-content-${index}" style="display: none;">
                    <div class="sourcemap-preview">
                        ${this.escapeHtml(file.content ? file.content.substring(0, 1000) : 'No content available')}
                        ${file.content && file.content.length > 1000 ? '\n... (truncated)' : ''}
                    </div>
                </div>
            </div>
        `).join('');
    }

    toggleSecret(index, value) {
        const element = document.getElementById(`secret-${index}`);
        const textSpan = element.querySelector('.secret-text');
        const toggleBtn = element.querySelector('.secret-toggle i');
        
        if (element.classList.contains('masked')) {
            element.classList.remove('masked');
            textSpan.textContent = value;
            toggleBtn.className = 'fas fa-eye-slash';
        } else {
            element.classList.add('masked');
            textSpan.textContent = '*'.repeat(Math.min(value.length, 20));
            toggleBtn.className = 'fas fa-eye';
        }
    }

    toggleSourceMapFile(index) {
        const content = document.getElementById(`sourcemap-content-${index}`);
        const chevron = document.getElementById(`sourcemap-chevron-${index}`);
        
        if (content.style.display === 'none') {
            content.style.display = 'block';
            chevron.className = 'fas fa-chevron-up';
        } else {
            content.style.display = 'none';
            chevron.className = 'fas fa-chevron-down';
        }
    }

    switchTab(tabName, options = {}) {
        const { pushHistory = true } = options;
        if (tabName !== 'files') {
            this.stopFileStatusPolling();
        }
        // Hide all tabs
        document.querySelectorAll('.main-tab-content').forEach(tab => {
            tab.style.display = 'none';
        });
        
        // Show selected tab
        const targetTab = document.getElementById(`${tabName}-tab`);
        if (targetTab) {
            targetTab.style.display = 'block';
            this.activeTab = tabName;
            if (pushHistory) {
                const query = {};
                if (tabName === 'files' && this.activeFilesSessionId) {
                    query.session_id = this.activeFilesSessionId;
                    query.session_name = this.activeFilesSessionName || '';
                }
                this.updateBrowserRoute(tabName, { query });
            }
            
            // Load content for specific tabs
            if (tabName === 'files') {
                this.loadFiles();
                this.startFileStatusPolling();
            } else if (tabName === 'sessions') {
                this.refreshActiveReconJobs({ silentErrors: true })
                    .finally(() => this.loadSessions());
            }
        }

        // Sync nav rail active state
        document.querySelectorAll('.nav-item[id^="nav-"]').forEach(el => el.classList.remove('active'));
        const activeNav = document.getElementById('nav-' + tabName);
        if (activeNav) activeNav.classList.add('active');
    }

    async refreshActiveReconJobs(options = {}) {
        const { silentErrors = false } = options;
        try {
            const response = await axios.get(`${this.apiBase}/api/recon/jobs`);
            const jobs = Array.isArray(response?.data?.jobs) ? response.data.jobs : [];
            const bySession = new Map();
            jobs.forEach((job) => {
                const sessionId = job?.sessionId;
                if (!sessionId) return;
                const current = bySession.get(sessionId);
                const currentTs = current?.updatedAt || current?.finishedAt || current?.startedAt || current?.createdAt || '';
                const nextTs = job?.updatedAt || job?.finishedAt || job?.startedAt || job?.createdAt || '';
                if (!current || nextTs >= currentTs) {
                    bySession.set(sessionId, job);
                }

                const status = String(job?.status || '').toLowerCase();
                if (['queued', 'running', 'cancelling'].includes(status) && job?.jobId) {
                    this.startReconJobPolling(job.jobId, sessionId);
                }
            });

            this.reconSessionProgress = bySession;
        } catch (error) {
            if (!silentErrors) {
                const detail = error?.response?.data?.detail;
                const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
                this.showAlert(`Recon jobs refresh failed: ${message}`, 'warning');
            }
        }
    }

    async loadFiles() {
        const container = document.getElementById('files-content');
        container.innerHTML = '<p class="text-center text-muted">Loading files...</p>';
        this.updateFilesScopeUI();
        
        try {
            let files = [];

            if (this.activeFilesSessionId) {
                try {
                    const filesResponse = await axios.get(`${this.apiBase}/api/sessions/${this.activeFilesSessionId}/files`, {
                        params: { dedupe: true }
                    });
                    const sessionFiles = Array.isArray(filesResponse.data) ? filesResponse.data : [];
                    files = sessionFiles.map((file) => ({ ...file, sessionId: this.activeFilesSessionId }));
                } catch (error) {
                    if (error?.response?.status === 404) {
                        this.showAlert('Selected session no longer exists. Showing all sessions.', 'warning');
                        this.clearFilesSessionFilter(false);
                        return this.loadFiles();
                    }
                    throw error;
                }
            } else {
                const sessionsResponse = await axios.get(`${this.apiBase}/api/sessions`);
                const sessions = Array.isArray(sessionsResponse.data) ? sessionsResponse.data : [];

                if (sessions.length === 0) {
                    this.visibleFileIds = [];
                    this.selectedFileIds.clear();
                    this.fileStatusSnapshot.clear();
                    this.renderFilesBulkActions();
                    container.innerHTML = this.getEmptyState('No files found. Upload some JavaScript files for analysis.', 'folder');
                    return;
                }

                const fileResponses = await Promise.all(
                    sessions.map(async (session) => {
                        const filesResponse = await axios.get(`${this.apiBase}/api/sessions/${session.id}/files`, {
                            params: { dedupe: true }
                        });
                        const filesForSession = Array.isArray(filesResponse.data) ? filesResponse.data : [];
                        return filesForSession.map((file) => ({ ...file, sessionId: session.id }));
                    })
                );

                files = fileResponses.flat();
            }

            files = this.dedupeDisplayedFiles(files);
            files = files.sort((a, b) => new Date(b.capturedAt || 0) - new Date(a.capturedAt || 0));
            const totalBeforeFilters = files.length;
            files = this.applyFileFilters(files);

            if (files.length === 0) {
                this.visibleFileIds = [];
                this.selectedFileIds.clear();
                this.fileStatusSnapshot.clear();
                this.renderFilesBulkActions();
                this.renderSourcemapValidationSummary([]);
                const emptyText = totalBeforeFilters === 0
                    ? (this.activeFilesSessionId
                        ? 'No files captured in this session.'
                        : 'No files found. Upload some JavaScript files for analysis.')
                    : 'No files match the current filters.';
                container.innerHTML = this.getEmptyState(emptyText, 'folder');
                return;
            }

            this.visibleFileIds = files.map((file) => file.id);
            this.selectedFileIds = new Set(
                Array.from(this.selectedFileIds).filter((id) => this.visibleFileIds.includes(id))
            );
            this.renderFilesBulkActions();
            await this.renderSourcemapValidationSummary(files);

            container.innerHTML = files.map(file => {
                const status = this.runningFileAnalyses.has(file.id) ? 'analyzing' : (file.analysisStatus || 'not_analyzed');
                const isBusy = status === 'analyzing';
                const isSelected = this.selectedFileIds.has(file.id);
                const failureInfo = this.deriveFailureInfo(file);
                const statusBadge = this.renderAnalysisStatusBadge(status, file.analysisError, failureInfo);
                const primaryLabel = status === 'completed' ? 'Reanalyze' : 'Analyze';
                const canViewResults = status === 'completed';
                const isProcessing = this.isFileStillProcessing({ ...file, analysisStatus: status });
                const retryButton = status === 'failed'
                    ? `<button class="btn btn-outline-danger btn-sm ms-2" ${isBusy ? 'disabled' : ''} onclick="dashboard.retryStoredFile('${file.id}')"><i class="fas fa-rotate-right me-1"></i>Retry</button>`
                    : '';
                const resultsButton = canViewResults
                    ? `<button class="btn btn-outline-primary btn-sm ms-2" onclick="dashboard.viewStoredAnalysis('${file.id}')"><i class="fas fa-chart-bar me-1"></i>View Results</button>`
                    : '';
                const sourcesButton = this.renderReconstructedSourcesButton(file.sourceMap);
                const analysisOverview = this.renderFileAnalysisOverview(file);
                const sourcemapBadge = this.renderSourcemapStatusBadge(file.sourceMap);
                const sourcemapLifecycle = this.renderSourcemapLifecycleLine(file.sourceMap);
                const failurePanel = this.renderFailurePanel(failureInfo);

                return `
                <div class="result-item" data-file-id="${file.id}" data-file-processing="${isProcessing ? 'true' : 'false'}">
                    <div class="result-header">
                        <div>
                            <h6 class="mb-1">
                                <input class="form-check-input row-select" type="checkbox" ${isSelected ? 'checked' : ''} onchange="dashboard.toggleFileSelection('${file.id}', this.checked)" />
                                <i class="fas fa-file-code me-2"></i>
                                <a href="${this.escapeHtml(file.url)}" target="_blank" rel="noopener noreferrer">
                                    ${this.escapeHtml(file.url)}
                                </a>
                            </h6>
                            <div>
                                <span class="badge bg-secondary me-2">${this.formatFileSize(file.contentLength || 0)}</span>
                                <span class="badge bg-info me-2">${this.escapeHtml(this.shortId(file.sessionId))}</span>
                                <span class="badge bg-dark">${this.escapeHtml(this.formatDateTime(file.capturedAt))}</span>
                                <span data-file-sourcemap-id="${file.id}">${sourcemapBadge}</span>
                                <span data-file-status-id="${file.id}">${statusBadge}</span>
                            </div>
                            <div data-file-lifecycle-id="${file.id}">${sourcemapLifecycle}</div>
                            <div data-file-overview-id="${file.id}">${analysisOverview}</div>
                            <div data-file-failure-id="${file.id}">${failurePanel}</div>
                        </div>
                        <div>
                            <button class="btn btn-success btn-sm" data-file-analyze-id="${file.id}" ${isBusy ? 'disabled' : ''} onclick="dashboard.analyzeStoredFile('${file.id}')">
                                <i class="fas fa-play me-1"></i>${primaryLabel}
                            </button>
                            <span data-file-view-id="${file.id}">${resultsButton}</span>
                            <span data-file-sources-id="${file.id}">${sourcesButton}</span>
                            <button class="btn btn-outline-danger btn-sm ms-2" data-file-delete-id="${file.id}" ${isBusy ? 'disabled' : ''} onclick="dashboard.deleteStoredFile('${file.id}')">
                                <i class="fas fa-trash me-1"></i>Delete
                            </button>
                            <span data-file-retry-id="${file.id}">${retryButton}</span>
                        </div>
                    </div>
                </div>
            `;
            }).join('');
            this.captureFileStatusSnapshot(files);
        } catch (error) {
            this.visibleFileIds = [];
            this.selectedFileIds.clear();
            this.fileStatusSnapshot.clear();
            this.renderFilesBulkActions();
            this.renderSourcemapValidationSummary([]);
            container.innerHTML = `<div class="alert alert-danger">Error loading files: ${error.message}</div>`;
        }
    }

    startFileStatusPolling() {
        if (this.fileStatusPoller) return;
        const tick = async () => {
            await this.pollFileStatusUpdates({ silentErrors: true });
        };
        tick();
        this.fileStatusPoller = window.setInterval(tick, 5000);
    }

    stopFileStatusPolling() {
        if (!this.fileStatusPoller) return;
        window.clearInterval(this.fileStatusPoller);
        this.fileStatusPoller = null;
        this.fileStatusPollingInFlight = false;
    }

    buildFileStatusSignature(file) {
        const analysisStatus = String(file?.analysisStatus || 'not_analyzed').toLowerCase();
        const analysisError = file?.analysisError || '';
        const sourceMap = file?.sourceMap || {};
        const sourceMapStatus = String(sourceMap?.processingStatus || 'none').toLowerCase();
        const sourceMapError = sourceMap?.processingError || '';
        const sourceMapValidation = sourceMap?.validation || {};
        const reconstructedFiles = Number(sourceMap?.reconstructedFilesCount) || 0;
        const counts = file?.analysisCounts || {};
        const endpoints = Number(counts?.endpoints) || 0;
        const secrets = Number(counts?.secrets) || 0;
        const dependencies = Number(counts?.dependencies) || 0;
        return [
            analysisStatus,
            analysisError,
            sourceMapStatus,
            sourceMapError,
            sourceMapValidation?.detected,
            sourceMapValidation?.fetched,
            sourceMapValidation?.http_status,
            sourceMapValidation?.json_valid,
            sourceMapValidation?.processed,
            sourceMapValidation?.failure_class,
            reconstructedFiles,
            endpoints,
            secrets,
            dependencies,
        ].join('|');
    }

    captureFileStatusSnapshot(files) {
        this.fileStatusSnapshot.clear();
        (files || []).forEach((file) => {
            if (!file?.id) return;
            this.fileStatusSnapshot.set(file.id, this.buildFileStatusSignature(file));
        });
    }

    isFileStillProcessing(file) {
        const analysisStatus = String(file?.analysisStatus || '').toLowerCase();
        const sourceMapStatus = String(file?.sourceMap?.processingStatus || '').toLowerCase();
        return ['queued', 'analyzing'].includes(analysisStatus) || ['pending', 'processing'].includes(sourceMapStatus);
    }

    hasVisibleProcessingRows() {
        const rows = document.querySelectorAll('#files-content [data-file-id]');
        if (!rows || rows.length === 0) return false;
        return Array.from(rows).some((row) => row.getAttribute('data-file-processing') === 'true');
    }

    async fetchFilesForPolling() {
        let files = [];
        if (this.activeFilesSessionId) {
            const filesResponse = await axios.get(`${this.apiBase}/api/sessions/${this.activeFilesSessionId}/files`, {
                params: { dedupe: true }
            });
            const sessionFiles = Array.isArray(filesResponse.data) ? filesResponse.data : [];
            files = sessionFiles.map((file) => ({ ...file, sessionId: this.activeFilesSessionId }));
        } else {
            const sessionsResponse = await axios.get(`${this.apiBase}/api/sessions`);
            const sessions = Array.isArray(sessionsResponse.data) ? sessionsResponse.data : [];
            if (sessions.length === 0) {
                return [];
            }

            const fileResponses = await Promise.all(
                sessions.map(async (session) => {
                    const filesResponse = await axios.get(`${this.apiBase}/api/sessions/${session.id}/files`, {
                        params: { dedupe: true }
                    });
                    const filesForSession = Array.isArray(filesResponse.data) ? filesResponse.data : [];
                    return filesForSession.map((file) => ({ ...file, sessionId: session.id }));
                })
            );
            files = fileResponses.flat();
        }

        files = this.dedupeDisplayedFiles(files);
        return files.sort((a, b) => new Date(b.capturedAt || 0) - new Date(a.capturedAt || 0));
    }

    patchStoredFileRow(file) {
        const fileId = file?.id;
        if (!fileId) return false;

        const row = document.querySelector(`#files-content [data-file-id="${fileId}"]`);
        if (!row) return false;

        const status = this.runningFileAnalyses.has(fileId) ? 'analyzing' : (file.analysisStatus || 'not_analyzed');
        const isBusy = status === 'analyzing' || status === 'queued';
        const failureInfo = this.deriveFailureInfo(file);

        const statusNode = row.querySelector(`[data-file-status-id="${fileId}"]`);
        const sourceMapNode = row.querySelector(`[data-file-sourcemap-id="${fileId}"]`);
        const lifecycleNode = row.querySelector(`[data-file-lifecycle-id="${fileId}"]`);
        const overviewNode = row.querySelector(`[data-file-overview-id="${fileId}"]`);
        const failureNode = row.querySelector(`[data-file-failure-id="${fileId}"]`);
        const analyzeButton = row.querySelector(`[data-file-analyze-id="${fileId}"]`);
        const deleteButton = row.querySelector(`[data-file-delete-id="${fileId}"]`);
        const viewNode = row.querySelector(`[data-file-view-id="${fileId}"]`);
        const sourcesNode = row.querySelector(`[data-file-sources-id="${fileId}"]`);
        const retryNode = row.querySelector(`[data-file-retry-id="${fileId}"]`);

        if (!statusNode || !sourceMapNode || !lifecycleNode || !overviewNode || !failureNode || !analyzeButton || !deleteButton || !viewNode || !sourcesNode || !retryNode) {
            return false;
        }

        statusNode.innerHTML = this.renderAnalysisStatusBadge(status, file.analysisError, failureInfo);
        sourceMapNode.innerHTML = this.renderSourcemapStatusBadge(file.sourceMap);
        lifecycleNode.innerHTML = this.renderSourcemapLifecycleLine(file.sourceMap);
        overviewNode.innerHTML = this.renderFileAnalysisOverview(file);
        failureNode.innerHTML = this.renderFailurePanel(failureInfo);

        const primaryLabel = status === 'completed' ? 'Reanalyze' : (isBusy ? 'Analyzing...' : 'Analyze');
        analyzeButton.disabled = isBusy;
        analyzeButton.innerHTML = `<i class="fas fa-play me-1"></i>${primaryLabel}`;
        deleteButton.disabled = isBusy;

        viewNode.innerHTML = status === 'completed'
            ? `<button class="btn btn-outline-primary btn-sm ms-2" onclick="dashboard.viewStoredAnalysis('${fileId}')"><i class="fas fa-chart-bar me-1"></i>View Results</button>`
            : '';
        sourcesNode.innerHTML = this.renderReconstructedSourcesButton(file.sourceMap);
        retryNode.innerHTML = status === 'failed'
            ? `<button class="btn btn-outline-danger btn-sm ms-2" onclick="dashboard.retryStoredFile('${fileId}')"><i class="fas fa-rotate-right me-1"></i>Retry</button>`
            : '';

        row.setAttribute('data-file-processing', this.isFileStillProcessing({ ...file, analysisStatus: status }) ? 'true' : 'false');
        return true;
    }

    async pollFileStatusUpdates(options = {}) {
        const { silentErrors = false } = options;
        if (this.activeTab !== 'files') return;
        if (this.fileStatusPollingInFlight) return;
        if (this.visibleFileIds.length === 0) return;
        if (!this.hasVisibleProcessingRows()) return;

        this.fileStatusPollingInFlight = true;
        try {
            const files = await this.fetchFilesForPolling();
            const filesById = new Map((files || []).map((file) => [file.id, file]));
            let requiresReload = files.length !== this.visibleFileIds.length;

            for (const fileId of this.visibleFileIds) {
                const latest = filesById.get(fileId);
                if (!latest) {
                    requiresReload = true;
                    continue;
                }

                const signature = this.buildFileStatusSignature(latest);
                const previous = this.fileStatusSnapshot.get(fileId);
                if (previous !== signature) {
                    const patched = this.patchStoredFileRow(latest);
                    this.fileStatusSnapshot.set(fileId, signature);
                    if (!patched) {
                        requiresReload = true;
                    }
                }
            }

            if (requiresReload) {
                await this.loadFiles();
            } else {
                const filteredFiles = this.applyFileFilters(files || []);
                await this.renderSourcemapValidationSummary(filteredFiles);
            }
        } catch (error) {
            if (error?.response?.status === 404 && this.activeFilesSessionId) {
                this.clearFilesSessionFilter(false);
                await this.loadFiles();
                return;
            }
            if (!silentErrors) {
                const detail = error?.response?.data?.detail;
                const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
                this.showAlert(`Files polling failed: ${message}`, 'danger');
            }
        } finally {
            this.fileStatusPollingInFlight = false;
        }
    }

    renderFilesBulkActions() {
        const node = document.getElementById('files-bulk-actions');
        if (!node) return;

        const selectedCount = this.selectedFileIds.size;
        const visibleCount = this.visibleFileIds.length;
        if (visibleCount === 0) {
            node.classList.add('d-none');
            node.innerHTML = '';
            return;
        }

        const allSelected = selectedCount > 0 && selectedCount === visibleCount;
        node.classList.remove('d-none');
        node.innerHTML = `
            <div class="bulk-actions-bar">
                <div class="bulk-actions-left">
                    <span class="badge bg-dark">${selectedCount} selected</span>
                    <button class="btn btn-outline-secondary btn-sm" onclick="dashboard.toggleSelectAllFiles()">
                        <i class="fas fa-check-double me-1"></i>${allSelected ? 'Unselect All' : 'Select All'}
                    </button>
                    <button class="btn btn-outline-secondary btn-sm" ${selectedCount === 0 ? 'disabled' : ''} onclick="dashboard.clearSelectedFiles()">
                        <i class="fas fa-xmark me-1"></i>Clear
                    </button>
                </div>
                <div class="bulk-actions-right">
                    <button class="btn btn-danger btn-sm" ${selectedCount === 0 ? 'disabled' : ''} onclick="dashboard.bulkDeleteSelectedFiles()">
                        <i class="fas fa-trash me-1"></i>Delete Selected
                    </button>
                </div>
            </div>
        `;
    }

    toggleFileSelection(fileId, checked) {
        if (!fileId) return;
        if (checked) {
            this.selectedFileIds.add(fileId);
        } else {
            this.selectedFileIds.delete(fileId);
        }
        this.renderFilesBulkActions();
    }

    toggleSelectAllFiles() {
        const visibleCount = this.visibleFileIds.length;
        if (visibleCount === 0) return;
        const allSelected = this.selectedFileIds.size === visibleCount;
        if (allSelected) {
            this.selectedFileIds.clear();
        } else {
            this.selectedFileIds = new Set(this.visibleFileIds);
        }
        this.renderFilesBulkActions();
        if (this.activeTab === 'files') {
            this.loadFiles();
        }
    }

    clearSelectedFiles() {
        this.selectedFileIds.clear();
        this.renderFilesBulkActions();
        if (this.activeTab === 'files') {
            this.loadFiles();
        }
    }

    async bulkDeleteSelectedFiles() {
        const fileIds = Array.from(this.selectedFileIds);
        if (fileIds.length === 0) return;
        if (!window.confirm(`Delete ${fileIds.length} selected file(s)? This cannot be undone.`)) {
            return;
        }

        try {
            const response = await axios.post(`${this.apiBase}/api/files/bulk-delete`, {
                fileIds,
            });
            const data = response.data || {};
            const deleted = Array.isArray(data.deleted) ? data.deleted : [];
            const failed = Array.isArray(data.failed) ? data.failed : [];

            deleted.forEach((id) => this.selectedFileIds.delete(id));
            await this.loadStatistics();
            if (this.activeTab === 'files') {
                await this.loadFiles();
            }
            if (this.activeTab === 'sessions') {
                await this.loadSessions();
            }

            if (failed.length === 0) {
                this.showAlert(`Deleted ${deleted.length} file(s).`, 'success');
            } else {
                this.showAlert(`Bulk delete completed with partial failures: ${deleted.length} deleted, ${failed.length} failed.`, 'warning');
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Bulk file deletion failed: ${message}`, 'danger');
        }
    }

    dedupeDisplayedFiles(files) {
        const statusRank = {
            completed: 4,
            analyzing: 3,
            failed: 2,
            not_analyzed: 1
        };

        const selected = new Map();
        for (const file of files || []) {
            const fallbackKey = `${file.url || ''}|${file.contentLength || 0}`;
            const key = `${file.sessionId || ''}::${file.contentHash || fallbackKey}`;
            const current = selected.get(key);

            if (!current) {
                selected.set(key, file);
                continue;
            }

            const currentRank = statusRank[current.analysisStatus || 'not_analyzed'] || 0;
            const nextRank = statusRank[file.analysisStatus || 'not_analyzed'] || 0;
            if (nextRank > currentRank) {
                selected.set(key, file);
                continue;
            }
            if (nextRank < currentRank) {
                continue;
            }

            const currentTime = new Date(current.capturedAt || 0).getTime();
            const nextTime = new Date(file.capturedAt || 0).getTime();
            if (nextTime >= currentTime) {
                selected.set(key, file);
            }
        }

        return Array.from(selected.values());
    }

    updateFilesScopeUI() {
        const label = document.getElementById('files-scope-label');
        const clearButton = document.getElementById('clear-files-filter-btn');
        if (!label || !clearButton) return;

        if (this.activeFilesSessionId) {
            const fallbackName = `Session ${this.shortId(this.activeFilesSessionId)}`;
            const displayName = this.activeFilesSessionName || fallbackName;
            label.textContent = `Scope: ${displayName}`;
            clearButton.style.display = 'inline-block';
        } else {
            label.textContent = 'Scope: All Sessions';
            clearButton.style.display = 'none';
        }
    }

    openSessionFiles(sessionId, encodedName = '') {
        if (!sessionId) return;
        const decodedName = encodedName ? decodeURIComponent(encodedName) : '';
        this.activeFilesSessionId = sessionId;
        this.activeFilesSessionName = decodedName || `Session ${this.shortId(sessionId)}`;
        this.switchTab('files');
    }

    clearFilesSessionFilter(reload = true) {
        this.activeFilesSessionId = null;
        this.activeFilesSessionName = null;
        this.updateFilesScopeUI();
        this.updateBrowserRoute(this.activeTab === 'files' ? 'files' : this.activeTab, {
            replace: this.activeTab === 'files',
            query: {},
        });
        if (reload && this.activeTab === 'files') {
            this.loadFiles();
        }
    }

    async analyzeStoredFile(fileId) {
        this.openFileAnalyzeConfig(fileId);
    }

    async startStoredFileAnalysisWithOptions(fileId, options = {}) {
        if (!fileId) return;
        if (this.runningFileAnalyses.has(fileId)) return;
        this.runningFileAnalyses.add(fileId);
        if (this.activeTab === 'files') {
            await this.loadFiles();
        }
        this.showLoadingModal(true);
        try {
            const response = await axios.post(`${this.apiBase}/api/files/${fileId}/analyze`, { options });
            const hydrated = await this.viewStoredAnalysis(fileId, {
                updateRoute: true,
                silent: true,
                showLoading: false,
            });
            this.applyAnalysisOptionsToForm(options);
            if (!hydrated) {
                this.currentResults = response.data;
                this.switchTab('analysis');
                this.displayResults(response.data, response.data.processing_time_ms || 0);
            } else {
                this.showAlert('File analysis completed successfully.', 'success');
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`File analysis failed: ${message}`, 'danger');
        } finally {
            this.runningFileAnalyses.delete(fileId);
            this.showLoadingModal(false);
            this.loadStatistics();
            if (this.activeTab === 'files') {
                await this.loadFiles();
            }
            if (this.activeTab === 'sessions') {
                await this.loadSessions();
            }
        }
    }

    async retryStoredFile(fileId) {
        this.openFileAnalyzeConfig(fileId);
    }

    async viewStoredAnalysis(fileId, options = {}) {
        if (!fileId) return;
        const { updateRoute = true, silent = false, showLoading = true } = options;
        if (showLoading) {
            this.showLoadingModal(true);
        }
        let loaded = false;
        try {
            const [analysisResponse, fileResponse] = await Promise.all([
                axios.get(`${this.apiBase}/api/files/${fileId}/analysis`),
                axios.get(`${this.apiBase}/api/files/${fileId}`),
            ]);

            let sourceMapPayload = null;
            try {
                const sourceMapResponse = await axios.get(`${this.apiBase}/api/files/${fileId}/sourcemap-content`);
                sourceMapPayload = sourceMapResponse.data || null;
            } catch (error) {
                sourceMapPayload = null;
            }

            const data = analysisResponse.data || {};
            const fileMeta = this.normalizeStoredFileMeta(fileResponse.data || {}, data);
            const contentResult = await this.fetchStoredFileContent(fileId);
            const fileContent = contentResult.loaded ? contentResult.content : '';
            const sourceUrl = fileMeta.url || data?.metadata?.url || '';

            this.currentResults = data;
            this.switchTab('analysis', { pushHistory: false });
            this.populateAnalysisInputs(sourceUrl, fileContent);

            const processingTime = Number(data?.stats?.processing_time_ms) || 0;
            const context = this.buildResultsContext(fileMeta, fileId, sourceMapPayload, {
                analysisData: data,
                sourceContentLoaded: contentResult.loaded,
            });
            this.displayResults(data, processingTime, context);

            if (updateRoute) {
                this.updateBrowserRoute('analysis', {
                    query: {
                        file_id: fileId,
                        session_id: fileMeta.sessionId || data?.sessionId || '',
                    },
                });
            }

            if (!silent) {
                const message = contentResult.loaded
                    ? 'Loaded stored analysis results.'
                    : `Loaded stored analysis results. Stored source content unavailable (${contentResult.error || 'unknown error'}).`;
                this.showAlert(message, contentResult.loaded ? 'success' : 'warning');
            }
            loaded = true;
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Unable to load stored analysis: ${message}`, 'warning');
        } finally {
            if (showLoading) {
                this.showLoadingModal(false);
            }
        }
        return loaded;
    }

    async deleteStoredFile(fileId) {
        if (!fileId) return;
        if (!window.confirm('Delete this file and its stored analysis results? This cannot be undone.')) {
            return;
        }

        try {
            await axios.delete(`${this.apiBase}/api/files/${fileId}`);
            this.selectedFileIds.delete(fileId);
            this.renderFilesBulkActions();
            this.showAlert('File deleted successfully.', 'success');
            await this.loadStatistics();
            if (this.activeTab === 'files') {
                await this.loadFiles();
            }
            if (this.activeTab === 'sessions') {
                await this.loadSessions();
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`File deletion failed: ${message}`, 'danger');
        }
    }

    async loadSessions() {
        const container = document.getElementById('sessions-content');
        container.innerHTML = '<p class="text-center text-muted">Loading sessions...</p>';
        
        try {
            const response = await axios.get(`${this.apiBase}/api/sessions`);
            const sessionsRaw = Array.isArray(response.data) ? response.data : [];
            const sessions = this.applySessionFilters(sessionsRaw);

            if (sessions.length === 0) {
                this.visibleSessionIds = [];
                this.selectedSessionIds.clear();
                this.renderSessionsBulkActions();
                const emptyText = sessionsRaw.length === 0
                    ? 'No sessions found. Start an analysis to create your first session.'
                    : 'No sessions match the current filters.';
                container.innerHTML = this.getEmptyState(emptyText, 'history');
                return;
            }

            this.visibleSessionIds = sessions.map((session) => session.id);
            this.selectedSessionIds = new Set(
                Array.from(this.selectedSessionIds).filter((id) => this.visibleSessionIds.includes(id))
            );
            this.renderSessionsBulkActions();

            container.innerHTML = sessions.map(session => {
                const progressState = this.sessionAnalysisProgress.get(session.id) || null;
                const progressStatus = String(progressState?.jobStatus || '').toLowerCase();
                const isProgressActive = progressState && ['queued', 'running', 'cancelling'].includes(progressStatus);
                const analysisBusy = this.runningSessionAnalyses.has(session.id) || isProgressActive;
                const stopping = progressStatus === 'cancelling';
                const reconState = this.reconSessionProgress.get(session.id) || null;
                const reconStatus = String(reconState?.status || '').toLowerCase();
                const reconBusy = ['queued', 'running', 'cancelling'].includes(reconStatus);
                const rowBusy = analysisBusy || reconBusy;
                const isSelected = this.selectedSessionIds.has(session.id);
                const defaultName = `Session ${this.shortId(session.id)}`;
                const displayName = (session.name && session.name.trim()) ? session.name.trim() : defaultName;
                const encodedName = encodeURIComponent(displayName);
                const escapedDisplayName = this.escapeHtml(displayName);
                const progressBadges = this.renderSessionProgressBadges(progressState);
                const reconBadges = this.renderReconProgressBadges(reconState);
                const analysisSummary = session.analysisSummary || {};
                const analysisCompleted = Number(analysisSummary.completed) || 0;
                const analysisFailed = Number(analysisSummary.failed) || 0;
                const analysisPerformed = Boolean(analysisSummary.performed);
                const captureCoverage = session.captureCoverage || null;
                const captureCoverageBadges = this.renderSessionCaptureCoverageBadges(captureCoverage);
                const analysisStatusBadge = analysisPerformed
                    ? `<span class="badge bg-success me-2">Analysis performed</span>
                       <span class="badge bg-info text-dark me-2">Done ${analysisCompleted}</span>
                       <span class="badge bg-danger me-2">Failed ${analysisFailed}</span>`
                    : '<span class="badge bg-secondary me-2">No analysis yet</span>';
                return `
                <div class="result-item" data-session-id="${session.id}">
                    <div class="result-header">
                        <div>
                            <h6 class="mb-2">
                                <input class="form-check-input row-select" type="checkbox" ${isSelected ? 'checked' : ''} onchange="dashboard.toggleSessionSelection('${session.id}', this.checked)" />
                                <i class="fas fa-history me-2"></i>
                                <span
                                    id="session-name-display-${session.id}"
                                    class="session-name-display"
                                    data-current-name="${escapedDisplayName}"
                                    data-editing="false"
                                    data-saving="false"
                                    ondblclick="dashboard.startInlineSessionRename('${session.id}')"
                                    title="Double-click to rename session"
                                    onkeydown="dashboard.handleInlineSessionRenameKeydown('${session.id}', event)"
                                    onblur="dashboard.commitInlineSessionRename('${session.id}')"
                                >${escapedDisplayName}</span>
                                <button type="button"
                                    class="session-name-edit-trigger"
                                    title="Rename session"
                                    onclick="dashboard.startInlineSessionRename('${session.id}')">
                                    <i class="fas fa-pen"></i>
                                </button>
                            </h6>
                            <div>
                                <span class="badge bg-primary me-2">${Number(session.fileCount) || 0} files</span>
                                <span class="badge bg-dark">${this.escapeHtml(this.formatDateTime(session.createdAt))}</span>
                                ${analysisStatusBadge}
                                ${captureCoverageBadges}
                                <span data-session-progress-id="${session.id}">${progressBadges}</span>
                                <span data-session-recon-id="${session.id}">${reconBadges}</span>
                            </div>
                        </div>
                        <div>
                            <button class="btn btn-success btn-sm" data-session-analyze-id="${session.id}" ${analysisBusy ? 'disabled' : ''} onclick="dashboard.analyzeSession('${session.id}')">
                                <i class="fas fa-bolt me-1"></i>${analysisBusy ? (stopping ? 'Stopping...' : 'Analyzing...') : 'Analyze All'}
                            </button>
                            <button class="btn btn-warning btn-sm ms-2 ${analysisBusy ? '' : 'd-none'}" data-session-stop-id="${session.id}" ${stopping ? 'disabled' : ''} onclick="dashboard.stopSessionAnalysis('${session.id}')">
                                <i class="fas fa-stop me-1"></i>${stopping ? 'Stopping...' : 'Stop'}
                            </button>
                            <button class="btn btn-primary btn-sm ms-2" onclick="dashboard.openSessionFiles('${session.id}', '${encodedName}')">
                                <i class="fas fa-folder-open me-1"></i>Open Session
                            </button>
                            <button class="btn btn-outline-primary btn-sm ms-2" ${analysisPerformed ? '' : 'disabled'} onclick="dashboard.showSessionSummary('${session.id}', '${encodedName}')">
                                <i class="fas fa-list-check me-1"></i>View Summary
                            </button>
                            <button class="btn btn-outline-danger btn-sm ms-2" data-session-delete-id="${session.id}" ${rowBusy ? 'disabled' : ''} onclick="dashboard.deleteSession('${session.id}')">
                                <i class="fas fa-trash me-1"></i>Delete
                            </button>
                        </div>
                    </div>
                </div>
            `;
            }).join('');
        } catch (error) {
            this.visibleSessionIds = [];
            this.selectedSessionIds.clear();
            this.renderSessionsBulkActions();
            container.innerHTML = `<div class="alert alert-danger">Error loading sessions: ${error.message}</div>`;
        }
    }

    renderSessionProgressBadges(progressState) {
        if (!progressState || !progressState.counts) return '';

        const status = String(progressState.jobStatus || '').toLowerCase();
        const counts = progressState.counts || {};
        const total = Number(counts.total) || 0;
        const queued = Number(counts.queued) || 0;
        const analyzing = Number(counts.analyzing) || 0;
        const completed = Number(counts.completed) || 0;
        const failed = Number(counts.failed) || 0;
        const cancelled = Number(counts.cancelled) || 0;

        const statusBadge = status === 'running' || status === 'queued'
            ? '<span class="badge bg-warning text-dark ms-2">Live analysis</span>'
            : status === 'cancelling'
                ? '<span class="badge bg-warning text-dark ms-2">Stopping...</span>'
            : status === 'completed'
                ? '<span class="badge bg-success ms-2">Analysis complete</span>'
                : status === 'cancelled'
                    ? '<span class="badge bg-secondary ms-2">Analysis stopped</span>'
                : status === 'failed'
                    ? '<span class="badge bg-danger ms-2">Analysis failed</span>'
                    : '';

        if (status === 'idle') {
            return statusBadge;
        }

        const cancelledBadge = cancelled > 0
            ? `<span class="badge bg-secondary ms-2">Cancelled ${cancelled}</span>`
            : '';
        const runConfigBadges = this.renderSessionRunConfigBadges(progressState);

        return `${statusBadge}
            <span class="badge bg-secondary ms-2">Queued ${queued}</span>
            <span class="badge bg-info text-dark ms-2">Analyzing ${analyzing}</span>
            <span class="badge bg-success ms-2">Completed ${completed}/${total}</span>
            <span class="badge bg-danger ms-2">Failed ${failed}</span>
            ${cancelledBadge}
            ${runConfigBadges}`;
    }

    renderReconProgressBadges(reconState) {
        if (!reconState || typeof reconState !== 'object') return '';
        const status = String(reconState.status || '').toLowerCase();
        if (!status || status === 'idle') return '';

        const statusLabelMap = {
            queued: 'Crawl queued',
            running: 'Crawl running',
            cancelling: 'Crawl stopping',
            cancelled: 'Crawl stopped',
            completed: 'Crawl complete',
            failed: 'Crawl failed',
        };
        const statusClassMap = {
            queued: 'bg-secondary',
            running: 'bg-warning text-dark',
            cancelling: 'bg-warning text-dark',
            cancelled: 'bg-secondary',
            completed: 'bg-success',
            failed: 'bg-danger',
        };
        const statusLabel = statusLabelMap[status] || `Crawl ${status}`;
        const statusClass = statusClassMap[status] || 'bg-secondary';

        const coverage = reconState.coverage || {};
        const discoveredJs = Number(coverage.discovered_js || 0);
        const fetchedJs = Number(coverage.fetched_js || 0);
        const mapDetected = Number(coverage.map_detected || 0);
        const mapFetched = Number(coverage.map_fetched || 0);
        const assetCount = Number(reconState.assetCount || (Array.isArray(reconState.assets) ? reconState.assets.length : 0) || 0);
        const engine = String(reconState.options?.discoveryEngine || '').trim().toLowerCase();
        const engineLabel = engine ? `Engine ${engine}` : null;

        return `
            <span class="badge ${statusClass} ms-2">${this.escapeHtml(statusLabel)}</span>
            <span class="badge bg-light text-dark border border-secondary ms-2">Assets ${assetCount}</span>
            <span class="badge bg-light text-dark border border-info ms-2">JS ${fetchedJs}/${discoveredJs}</span>
            <span class="badge bg-light text-dark border border-primary ms-2">Maps ${mapFetched}/${mapDetected}</span>
            ${engineLabel ? `<span class="badge bg-light text-dark border border-dark ms-2">${this.escapeHtml(engineLabel)}</span>` : ''}
        `;
    }

    renderSessionRunConfigBadges(progressState) {
        const options = progressState?.options;
        if (!options || typeof options !== 'object') return '';

        const mode = String(options.run_mode || 'advanced').toLowerCase();
        const analysisType = String(options.analysis_type || 'comprehensive').toLowerCase();
        const includeSourcemap = options.include_sourcemap !== false;
        const failFast = options.continue_on_error === false;
        const maxFiles = Number(options.max_files_to_analyze) || 0;
        const maxFailures = Number(options.max_failures) || 0;

        const modeLabel = mode === 'quick' ? 'Quick' : 'Advanced';
        const typeLabel = analysisType === 'jsluice' ? 'JSluice-only' : 'Comprehensive';
        const sourcemapLabel = includeSourcemap ? 'Maps on' : 'Maps off';
        const policyLabel = failFast ? 'Fail-fast' : 'Continue-on-error';

        const detailParts = [
            `mode=${modeLabel}`,
            `type=${typeLabel}`,
            `maps=${includeSourcemap ? 'on' : 'off'}`,
            `policy=${policyLabel}`,
        ];
        if (maxFiles > 0) detailParts.push(`maxFiles=${maxFiles}`);
        if (maxFailures > 0) detailParts.push(`maxFailures=${maxFailures}`);
        const title = this.escapeHtml(detailParts.join(', '));

        return `
            <span class="badge bg-light text-dark border border-primary ms-2" title="${title}">${modeLabel}</span>
            <span class="badge bg-light text-dark border border-info ms-2" title="${title}">${typeLabel}</span>
            <span class="badge bg-light text-dark border border-secondary ms-2" title="${title}">${sourcemapLabel}</span>
            <span class="badge bg-light text-dark border border-warning ms-2" title="${title}">${policyLabel}</span>
        `;
    }

    renderSessionCaptureCoverageBadges(captureCoverage) {
        if (!captureCoverage) return '';

        const discovered = Number(captureCoverage.discovered_js) || 0;
        if (discovered <= 0) return '';

        const analyzed = Number(captureCoverage.analyzed_js) || 0;
        const mapDetected = Number(captureCoverage.map_detected) || 0;
        const mapFetched = Number(captureCoverage.map_fetched) || 0;
        const rates = captureCoverage.rates || {};
        const analysisPct = Number(rates.analysisPct) || 0;
        const mapFetchPct = Number(rates.mapFetchPct) || 0;
        const reasons = captureCoverage.failure_reasons || {};
        const reasonSummary = Object.entries(reasons)
            .filter(([, value]) => (Number(value) || 0) > 0)
            .sort((a, b) => (Number(b[1]) || 0) - (Number(a[1]) || 0))
            .slice(0, 2)
            .map(([key, value]) => `${key}:${value}`)
            .join(', ');
        const reasonTitle = reasonSummary ? `Top miss reasons: ${reasonSummary}` : 'No miss reasons recorded';

        return `
            <span class="badge bg-light text-dark border border-info me-2" title="Analyzed ${analyzed} of ${discovered} discovered JS files.">
                Capture ${analysisPct.toFixed(1)}%
            </span>
            <span class="badge bg-light text-dark border border-secondary me-2" title="Fetched ${mapFetched} of ${mapDetected} detected sourcemaps. ${reasonTitle}">
                Maps ${mapFetchPct.toFixed(1)}%
            </span>
        `;
    }

    renderSessionsBulkActions() {
        const node = document.getElementById('sessions-bulk-actions');
        if (!node) return;

        const selectedCount = this.selectedSessionIds.size;
        const visibleCount = this.visibleSessionIds.length;
        if (visibleCount === 0) {
            node.classList.add('d-none');
            node.innerHTML = '';
            return;
        }

        const allSelected = selectedCount > 0 && selectedCount === visibleCount;
        node.classList.remove('d-none');
        node.innerHTML = `
            <div class="bulk-actions-bar">
                <div class="bulk-actions-left">
                    <span class="badge bg-dark">${selectedCount} selected</span>
                    <button class="btn btn-outline-secondary btn-sm" onclick="dashboard.toggleSelectAllSessions()">
                        <i class="fas fa-check-double me-1"></i>${allSelected ? 'Unselect All' : 'Select All'}
                    </button>
                    <button class="btn btn-outline-secondary btn-sm" ${selectedCount === 0 ? 'disabled' : ''} onclick="dashboard.clearSelectedSessions()">
                        <i class="fas fa-xmark me-1"></i>Clear
                    </button>
                </div>
                <div class="bulk-actions-right">
                    <button class="btn btn-danger btn-sm" ${selectedCount === 0 ? 'disabled' : ''} onclick="dashboard.bulkDeleteSelectedSessions()">
                        <i class="fas fa-trash me-1"></i>Delete Selected
                    </button>
                </div>
            </div>
        `;
    }

    toggleSessionSelection(sessionId, checked) {
        if (!sessionId) return;
        if (checked) {
            this.selectedSessionIds.add(sessionId);
        } else {
            this.selectedSessionIds.delete(sessionId);
        }
        this.renderSessionsBulkActions();
    }

    toggleSelectAllSessions() {
        const visibleCount = this.visibleSessionIds.length;
        if (visibleCount === 0) return;
        const allSelected = this.selectedSessionIds.size === visibleCount;
        if (allSelected) {
            this.selectedSessionIds.clear();
        } else {
            this.selectedSessionIds = new Set(this.visibleSessionIds);
        }
        this.renderSessionsBulkActions();
        if (this.activeTab === 'sessions') {
            this.loadSessions();
        }
    }

    clearSelectedSessions() {
        this.selectedSessionIds.clear();
        this.renderSessionsBulkActions();
        if (this.activeTab === 'sessions') {
            this.loadSessions();
        }
    }

    async bulkDeleteSelectedSessions() {
        const sessionIds = Array.from(this.selectedSessionIds);
        if (sessionIds.length === 0) return;
        if (!window.confirm(`Delete ${sessionIds.length} selected session(s) and all files inside? This cannot be undone.`)) {
            return;
        }

        try {
            const response = await axios.post(`${this.apiBase}/api/sessions/bulk-delete`, {
                sessionIds,
            });
            const data = response.data || {};
            const deleted = Array.isArray(data.deleted) ? data.deleted : [];
            const failed = Array.isArray(data.failed) ? data.failed : [];

            deleted.forEach((id) => {
                this.selectedSessionIds.delete(id);
                this.stopSessionProgressPolling(id);
                this.stopReconPollingForSession(id);
                this.runningSessionAnalyses.delete(id);
                this.sessionAnalysisProgress.delete(id);
                this.sessionCompletionNotified.delete(id);
                if (this.activeFilesSessionId === id) {
                    this.clearFilesSessionFilter(false);
                }
            });

            await this.loadStatistics();
            if (this.activeTab === 'sessions') {
                await this.loadSessions();
            }
            if (this.activeTab === 'files') {
                await this.loadFiles();
            }

            if (failed.length === 0) {
                this.showAlert(`Deleted ${deleted.length} session(s).`, 'success');
            } else {
                this.showAlert(`Bulk session delete completed with partial failures: ${deleted.length} deleted, ${failed.length} failed.`, 'warning');
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Bulk session deletion failed: ${message}`, 'danger');
        }
    }

    startInlineSessionRename(sessionId) {
        const display = document.getElementById(`session-name-display-${sessionId}`);
        if (!display) return;
        if (display.dataset.saving === 'true' || display.dataset.editing === 'true') return;

        const currentName = (display.dataset.currentName || display.textContent || '').trim();
        display.dataset.originalName = currentName;
        display.dataset.editing = 'true';
        display.setAttribute('contenteditable', 'true');
        display.classList.add('is-editing');
        display.focus();
        this.placeCaretAtEnd(display);
    }

    handleInlineSessionRenameKeydown(sessionId, event) {
        if (!event) return;
        if (event.key === 'Enter') {
            event.preventDefault();
            this.commitInlineSessionRename(sessionId);
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            this.cancelInlineSessionRename(sessionId);
        }
    }

    cancelInlineSessionRename(sessionId) {
        const display = document.getElementById(`session-name-display-${sessionId}`);
        if (!display) return;

        const originalName = (display.dataset.originalName || display.dataset.currentName || display.textContent || '').trim();
        display.textContent = originalName;
        this.finishInlineSessionRename(display);
    }

    async commitInlineSessionRename(sessionId) {
        const display = document.getElementById(`session-name-display-${sessionId}`);
        if (!display) return;
        if (display.dataset.editing !== 'true') return;
        if (display.dataset.saving === 'true') return;

        const originalName = (display.dataset.originalName || display.dataset.currentName || '').trim();
        const nextName = (display.textContent || '').trim();

        if (!nextName) {
            this.showAlert('Session name cannot be empty.', 'warning');
            display.textContent = originalName;
            this.finishInlineSessionRename(display);
            return;
        }

        if (nextName === originalName) {
            this.finishInlineSessionRename(display);
            return;
        }

        display.dataset.saving = 'true';
        display.classList.add('is-saving');
        try {
            await axios.patch(`${this.apiBase}/api/sessions/${sessionId}`, { name: nextName });

            display.textContent = nextName;
            display.dataset.currentName = nextName;

            if (this.activeFilesSessionId === sessionId) {
                this.activeFilesSessionName = nextName;
                this.updateFilesScopeUI();
            }

            this.showAlert('Session renamed successfully.', 'success');
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Session rename failed: ${message}`, 'danger');
            display.textContent = originalName;
        } finally {
            this.finishInlineSessionRename(display);
        }
    }

    finishInlineSessionRename(display) {
        display.removeAttribute('contenteditable');
        display.dataset.editing = 'false';
        display.dataset.saving = 'false';
        display.classList.remove('is-editing');
        display.classList.remove('is-saving');
        display.blur();
    }

    placeCaretAtEnd(element) {
        const selection = window.getSelection();
        if (!selection) return;
        const range = document.createRange();
        range.selectNodeContents(element);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
    }

    async analyzeSession(sessionId) {
        if (!sessionId) return;
        this.openSessionAnalyzeConfig(sessionId);
    }

    async stopSessionAnalysis(sessionId) {
        if (!sessionId) return;

        const progressState = this.sessionAnalysisProgress.get(sessionId) || null;
        const status = String(progressState?.jobStatus || '').toLowerCase();
        const active = this.runningSessionAnalyses.has(sessionId) || ['queued', 'running', 'cancelling'].includes(status);
        if (!active) {
            this.showAlert('No active session analysis to stop.', 'warning');
            return;
        }

        try {
            const response = await axios.post(`${this.apiBase}/api/sessions/${sessionId}/analyze/stop`);
            const data = response.data || {};
            const job = data.job || null;
            if (job) {
                this.sessionAnalysisProgress.set(sessionId, job);
            }

            if (data.stopRequested) {
                this.runningSessionAnalyses.add(sessionId);
                this.startSessionProgressPolling(sessionId);
                this.showAlert('Stop requested. Current file will finish, then analysis will halt.', 'warning');
            } else {
                this.runningSessionAnalyses.delete(sessionId);
                this.showAlert(data.message || 'No active session analysis to stop.', 'info');
            }

            await this.refreshViewsForSessionProgress(sessionId, {
                job: job || this.sessionAnalysisProgress.get(sessionId) || null,
                fullReload: false,
            });
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Stop analysis failed: ${message}`, 'danger');
        }
    }

    startSessionProgressPolling(sessionId) {
        if (!sessionId) return;
        if (this.sessionProgressPollers.has(sessionId)) return;

        const tick = async () => {
            await this.fetchSessionProgress(sessionId, { silentErrors: true });
        };

        tick();
        const intervalId = window.setInterval(tick, 2000);
        this.sessionProgressPollers.set(sessionId, intervalId);
    }

    stopSessionProgressPolling(sessionId) {
        const intervalId = this.sessionProgressPollers.get(sessionId);
        if (intervalId) {
            window.clearInterval(intervalId);
        }
        this.sessionProgressPollers.delete(sessionId);
        this.sessionPollingInFlight.delete(sessionId);
    }

    async fetchSessionProgress(sessionId, options = {}) {
        const { silentErrors = false } = options;
        if (!sessionId) return;
        if (this.sessionPollingInFlight.has(sessionId)) return;

        this.sessionPollingInFlight.add(sessionId);
        try {
            const response = await axios.get(`${this.apiBase}/api/sessions/${sessionId}/analyze/progress`);
            const job = response.data?.job || null;
            if (!job) {
                this.runningSessionAnalyses.delete(sessionId);
                this.stopSessionProgressPolling(sessionId);
                return;
            }

            this.sessionAnalysisProgress.set(sessionId, job);
            const status = String(job.jobStatus || '').toLowerCase();
            if (status === 'queued' || status === 'running' || status === 'cancelling') {
                this.runningSessionAnalyses.add(sessionId);
            } else {
                this.runningSessionAnalyses.delete(sessionId);
                this.stopSessionProgressPolling(sessionId);

                if (!this.sessionCompletionNotified.has(sessionId)) {
                    const summary = job.summary || {};
                    const analyzed = Number(summary.analyzed) || 0;
                    const failed = Number(summary.failed) || 0;
                    const cancelled = Number(summary.cancelled) || Number(job.counts?.cancelled) || 0;
                    const total = Number(job.counts?.total) || (analyzed + failed + cancelled);
                    if (status === 'cancelled') {
                        this.showAlert(
                            `Session analysis stopped: ${analyzed}/${total} analyzed, ${failed} failed, ${cancelled} cancelled.`,
                            'warning'
                        );
                    } else if (status === 'failed') {
                        this.showAlert(
                            `Session analysis failed: ${analyzed}/${total} analyzed, ${failed} failed.`,
                            'danger'
                        );
                    } else {
                        const message = `Session analysis complete: ${analyzed}/${total} analyzed, ${failed} failed.`;
                        this.showAlert(message, failed > 0 ? 'warning' : 'success');
                    }
                    this.sessionCompletionNotified.add(sessionId);
                }
            }

            const isTerminal = !['queued', 'running', 'cancelling'].includes(status);
            await this.refreshViewsForSessionProgress(sessionId, {
                job,
                fullReload: isTerminal,
            });
        } catch (error) {
            if (!silentErrors) {
                const detail = error?.response?.data?.detail;
                const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
                this.showAlert(`Progress polling failed: ${message}`, 'danger');
            }
            this.runningSessionAnalyses.delete(sessionId);
            this.stopSessionProgressPolling(sessionId);
        } finally {
            this.sessionPollingInFlight.delete(sessionId);
        }
    }

    async refreshViewsForSessionProgress(sessionId, options = {}) {
        const { job = null, fullReload = false } = options;
        const progressState = job || this.sessionAnalysisProgress.get(sessionId) || null;

        if (!fullReload) {
            if (this.activeTab === 'sessions') {
                this.patchSessionProgressRow(sessionId, progressState);
            }
            if (this.activeTab === 'files' && (!this.activeFilesSessionId || this.activeFilesSessionId === sessionId)) {
                this.patchFileProgressRows(progressState);
            }
            return;
        }

        await this.loadStatistics();
        if (this.activeTab === 'sessions') {
            await this.loadSessions();
        }
        if (this.activeTab === 'files' && (!this.activeFilesSessionId || this.activeFilesSessionId === sessionId)) {
            await this.loadFiles();
        }
    }

    patchSessionProgressRow(sessionId, progressState) {
        if (!sessionId) return false;
        const progressNode = document.querySelector(`[data-session-progress-id="${sessionId}"]`);
        const analyzeButton = document.querySelector(`[data-session-analyze-id="${sessionId}"]`);
        const stopButton = document.querySelector(`[data-session-stop-id="${sessionId}"]`);
        const deleteButton = document.querySelector(`[data-session-delete-id="${sessionId}"]`);

        if (!progressNode || !analyzeButton || !deleteButton) return false;

        progressNode.innerHTML = this.renderSessionProgressBadges(progressState);
        const status = String(progressState?.jobStatus || '').toLowerCase();
        const busy = this.runningSessionAnalyses.has(sessionId) || ['queued', 'running', 'cancelling'].includes(status);
        const reconState = this.reconSessionProgress.get(sessionId) || null;
        const reconStatus = String(reconState?.status || '').toLowerCase();
        const reconBusy = ['queued', 'running', 'cancelling'].includes(reconStatus);

        analyzeButton.disabled = busy;
        analyzeButton.innerHTML = `<i class="fas fa-bolt me-1"></i>${busy ? (status === 'cancelling' ? 'Stopping...' : 'Analyzing...') : 'Analyze All'}`;
        if (stopButton) {
            stopButton.classList.toggle('d-none', !busy);
            stopButton.disabled = status === 'cancelling' || !busy;
            stopButton.innerHTML = `<i class="fas fa-stop me-1"></i>${status === 'cancelling' ? 'Stopping...' : 'Stop'}`;
        }
        deleteButton.disabled = busy || reconBusy;
        this.patchSessionReconProgressRow(sessionId, reconState);
        return true;
    }

    patchSessionReconProgressRow(sessionId, reconState) {
        if (!sessionId) return false;
        const reconNode = document.querySelector(`[data-session-recon-id="${sessionId}"]`);
        const deleteButton = document.querySelector(`[data-session-delete-id="${sessionId}"]`);
        if (!reconNode) return false;

        reconNode.innerHTML = this.renderReconProgressBadges(reconState);
        const reconStatus = String(reconState?.status || '').toLowerCase();
        const reconBusy = ['queued', 'running', 'cancelling'].includes(reconStatus);
        if (deleteButton) {
            if (reconBusy) {
                deleteButton.disabled = true;
            }
        }
        return true;
    }

    patchFileProgressRows(progressState) {
        if (!progressState || !Array.isArray(progressState.files) || progressState.files.length === 0) return false;

        let updated = false;
        for (const fileState of progressState.files) {
            const fileId = fileState?.fileId;
            if (!fileId) continue;

            const status = String(fileState.status || '').toLowerCase();
            const statusNode = document.querySelector(`[data-file-status-id="${fileId}"]`);
            if (!statusNode) continue;

            const row = statusNode.closest('[data-file-id]');
            const analyzeButton = row ? row.querySelector(`[data-file-analyze-id="${fileId}"]`) : null;
            const deleteButton = row ? row.querySelector(`[data-file-delete-id="${fileId}"]`) : null;
            const overviewNode = row ? row.querySelector(`[data-file-overview-id="${fileId}"]`) : null;
            const viewNode = row ? row.querySelector(`[data-file-view-id="${fileId}"]`) : null;
            const sourcesNode = row ? row.querySelector(`[data-file-sources-id="${fileId}"]`) : null;
            const retryNode = row ? row.querySelector(`[data-file-retry-id="${fileId}"]`) : null;

            const error = typeof fileState.error === 'string' ? fileState.error : null;
            statusNode.innerHTML = this.renderAnalysisStatusBadge(status, error);
            if (overviewNode && status !== 'completed') {
                overviewNode.innerHTML = '';
            }

            const isBusy = status === 'analyzing' || status === 'queued' || status === 'cancelling';
            if (analyzeButton) {
                const primaryLabel = status === 'completed' ? 'Reanalyze' : (isBusy ? 'Analyzing...' : 'Analyze');
                analyzeButton.disabled = isBusy;
                analyzeButton.innerHTML = `<i class="fas fa-play me-1"></i>${primaryLabel}`;
            }
            if (deleteButton) {
                deleteButton.disabled = isBusy;
            }
            if (viewNode) {
                viewNode.innerHTML = status === 'completed'
                    ? `<button class="btn btn-outline-primary btn-sm ms-2" onclick="dashboard.viewStoredAnalysis('${fileId}')"><i class="fas fa-chart-bar me-1"></i>View Results</button>`
                    : '';
            }
            if (sourcesNode) {
                // For polling updates, we don't have the full file object, so we'll skip updating the sources button
                // The sources button will be updated properly during full refresh or when the file row is patched with complete data
            }
            if (retryNode) {
                retryNode.innerHTML = status === 'failed'
                    ? `<button class="btn btn-outline-danger btn-sm ms-2" onclick="dashboard.retryStoredFile('${fileId}')"><i class="fas fa-rotate-right me-1"></i>Retry</button>`
                    : '';
            }
            if (row) {
                row.setAttribute('data-file-processing', isBusy ? 'true' : 'false');
            }

            updated = true;
        }

        return updated;
    }

    async deleteSession(sessionId) {
        if (!sessionId) return;
        if (!window.confirm('Delete this entire session and all files in it? This cannot be undone.')) {
            return;
        }

        try {
            await axios.delete(`${this.apiBase}/api/sessions/${sessionId}`);
            this.stopSessionProgressPolling(sessionId);
            this.stopReconPollingForSession(sessionId);
            this.runningSessionAnalyses.delete(sessionId);
            this.sessionAnalysisProgress.delete(sessionId);
            this.sessionCompletionNotified.delete(sessionId);
            this.selectedSessionIds.delete(sessionId);
            this.renderSessionsBulkActions();
            if (this.activeFilesSessionId === sessionId) {
                this.clearFilesSessionFilter(false);
            }
            this.showAlert('Session deleted successfully.', 'success');
            await this.loadStatistics();
            if (this.activeTab === 'sessions') {
                await this.loadSessions();
            }
            if (this.activeTab === 'files') {
                await this.loadFiles();
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            this.showAlert(`Session deletion failed: ${message}`, 'danger');
        }
    }

    showLoadingModal(show) {
        const modal = document.getElementById('loadingModal');
        if (show) {
            modal.style.display = 'block';
            modal.classList.add('show');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('modal-open');
        } else {
            modal.style.display = 'none';
            modal.classList.remove('show');
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('modal-open');
        }
    }

    showAlert(message, type = 'info') {
        // Create alert element
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
        alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
        alertDiv.innerHTML = `
            ${message}
            <button type="button" class="btn-close" onclick="this.parentElement.remove()"></button>
        `;
        
        document.body.appendChild(alertDiv);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (alertDiv.parentElement) {
                alertDiv.remove();
            }
        }, 5000);
    }

    exportResults() {
        if (!this.currentResults) {
            this.showAlert('No results to export', 'warning');
            return;
        }
        
        const dataStr = JSON.stringify(this.currentResults, null, 2);
        const dataBlob = new Blob([dataStr], {type: 'application/json'});
        
        const link = document.createElement('a');
        link.href = URL.createObjectURL(dataBlob);
        link.download = `js-analysis-${new Date().getTime()}.json`;
        link.click();
    }

    getEmptyState(message, icon) {
        return `
            <div class="empty-state">
                <i class="fas fa-${icon}"></i>
                <p>${message}</p>
            </div>
        `;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    formatFileSize(bytes) {
        const size = Number(bytes);
        if (!Number.isFinite(size) || size <= 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(size) / Math.log(k));
        return parseFloat((size / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    shortId(id) {
        if (!id) return 'unknown';
        return String(id).slice(0, 8);
    }

    formatDateTime(value) {
        if (!value) return 'unknown';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString();
    }

    renderLocation(item) {
        const file = item.file || item.source_file || item.sourceFile || item.source_file_url || item.file_url || '';
        const lineValue = item.line || item.line_number || item.lineNumber || null;
        const line = lineValue ? `:${lineValue}` : '';
        if (!file && !line) return '';
        return `${file || 'unknown'}${line}`;
    }

    renderExtractorBadges(item) {
        const extractors = Array.isArray(item.extractors) && item.extractors.length > 0
            ? item.extractors
            : (item.extractor ? [item.extractor] : []);
        if (extractors.length === 0) return '';
        return extractors
            .slice(0, 3)
            .map(extractor => `<span class="badge bg-primary me-1">${this.escapeHtml(extractor)}</span>`)
            .join('');
    }

    renderFileAnalysisOverview(file) {
        const status = (file.analysisStatus || '').toLowerCase();
        if (status !== 'completed') return '';

        const counts = file.analysisCounts || {};
        const stats = file.analysisStats || {};
        const endpoints = Number(counts.endpoints) || Number(stats.total_endpoints) || 0;
        const secrets = Number(counts.secrets) || Number(stats.total_secrets) || 0;
        const dependencies = Number(counts.dependencies) || Number(stats.total_dependencies) || 0;
        const extractors = Array.isArray(file.analysisExtractors) ? file.analysisExtractors.slice(0, 3) : [];
        const extractorBadges = extractors.length > 0
            ? extractors.map(extractor => `<span class="badge bg-primary me-1">${this.escapeHtml(extractor)}</span>`).join('')
            : '';
        const processingTime = Number(stats.processing_time_ms) || 0;
        const processingBadge = processingTime > 0
            ? `<span class="badge bg-info text-dark me-1">${processingTime}ms</span>`
            : '';

        return `
            <div class="mt-2">
                <span class="badge bg-primary me-1">Endpoints ${endpoints}</span>
                <span class="badge bg-danger me-1">Secrets ${secrets}</span>
                <span class="badge bg-warning text-dark me-1">Dependencies ${dependencies}</span>
                ${processingBadge}
                ${extractorBadges}
            </div>
        `;
    }

    renderAnalysisStatusBadge(status, error, failureInfo = null) {
        const normalized = (status || 'not_analyzed').toLowerCase();
        if (normalized === 'completed') return '<span class="badge bg-success ms-2">Analyzed</span>';
        if (normalized === 'queued') return '<span class="badge bg-secondary ms-2">Queued</span>';
        if (normalized === 'analyzing') return '<span class="badge bg-warning text-dark ms-2">Analyzing</span>';
        if (normalized === 'cancelled') return '<span class="badge bg-secondary ms-2">Cancelled</span>';
        if (normalized === 'failed') {
            const title = error ? this.escapeHtml(error) : 'Analysis failed';
            const label = failureInfo?.label ? `Failed (${this.escapeHtml(failureInfo.label)})` : 'Failed';
            return `<span class="badge bg-danger ms-2" title="${title}">${label}</span>`;
        }
        return '<span class="badge bg-secondary ms-2">Not analyzed</span>';
    }

    extractFailureClassFromError(errorText) {
        const text = String(errorText || '').trim();
        const match = text.match(/^\[([a-z0-9_]+)\]/i);
        return match ? String(match[1]).toLowerCase() : null;
    }

    getSourcemapValidationState(sourceMap) {
        if (!sourceMap || typeof sourceMap !== 'object') {
            return {
                detected: false,
                fetched: false,
                http_status: null,
                content_type: null,
                json_valid: null,
                processed: false,
                failure_class: null,
            };
        }

        const status = String(sourceMap.processingStatus || '').toLowerCase();
        const validation = sourceMap.validation && typeof sourceMap.validation === 'object'
            ? sourceMap.validation
            : {};
        const failureClass = validation.failure_class || this.extractFailureClassFromError(sourceMap.processingError);
        const detected = validation.detected !== undefined
            ? Boolean(validation.detected)
            : Boolean(sourceMap.detectedMapUrl || sourceMap.mapUrl);
        const fetched = validation.fetched !== undefined
            ? Boolean(validation.fetched)
            : (detected && ['processing', 'completed', 'completed_limited', 'failed'].includes(status));
        const processed = validation.processed !== undefined
            ? Boolean(validation.processed)
            : ['completed', 'completed_limited'].includes(status);

        let jsonValid = validation.json_valid;
        if (jsonValid === undefined || jsonValid === null) {
            if (processed) {
                jsonValid = true;
            } else if (failureClass === 'decode_invalid_json' || failureClass === 'decode_content') {
                jsonValid = false;
            } else {
                jsonValid = null;
            }
        }

        return {
            detected,
            fetched,
            http_status: validation.http_status ?? null,
            content_type: validation.content_type ?? null,
            json_valid: jsonValid,
            processed,
            failure_class: failureClass || null,
        };
    }

    isProbableJavascriptFile(file) {
        const contentType = String(file?.contentType || '').toLowerCase();
        if (contentType.includes('javascript') || contentType.includes('ecmascript')) return true;
        const url = String(file?.url || '').toLowerCase();
        return url.includes('.js') || url.includes('.mjs') || url.includes('.jsx');
    }

    computeSourcemapValidationSummary(files) {
        const relevantFiles = (files || []).filter((file) => this.isProbableJavascriptFile(file));
        const states = relevantFiles.map((file) => this.getSourcemapValidationState(file?.sourceMap || null));
        const totalJs = states.length;
        const mapCandidates = states.filter((item) => item.detected).length;
        const mapFetched = states.filter((item) => item.fetched).length;
        const jsonValid = states.filter((item) => item.json_valid === true).length;
        const processed = states.filter((item) => item.processed).length;
        const failed = states.filter((item) => item.failure_class).length;
        const noMap = Math.max(0, totalJs - mapCandidates);

        const failureReasons = {};
        states.forEach((item) => {
            if (!item.failure_class) return;
            failureReasons[item.failure_class] = (failureReasons[item.failure_class] || 0) + 1;
        });

        const pct = (numerator, denominator) => {
            if (!denominator || denominator <= 0) return 0;
            return Math.round((numerator / denominator) * 10000) / 100;
        };

        return {
            denominators: {
                total_js: totalJs,
                map_candidates: mapCandidates,
                map_fetched: mapFetched,
                json_checked: mapFetched,
            },
            counts: {
                no_map_candidate: noMap,
                processed,
                failed,
                json_valid: jsonValid,
            },
            rates: {
                candidatePctOfJs: pct(mapCandidates, totalJs),
                fetchPctOfCandidates: pct(mapFetched, mapCandidates),
                processPctOfCandidates: pct(processed, mapCandidates),
                jsonValidPctOfFetched: pct(jsonValid, mapFetched),
            },
            failure_reasons: failureReasons,
        };
    }

    async renderSourcemapValidationSummary(files) {
        const node = document.getElementById('files-sourcemap-validation-summary');
        if (!node) return;

        const hasFiles = Array.isArray(files) && files.length > 0;
        if (!hasFiles) {
            node.classList.add('d-none');
            node.innerHTML = '';
            return;
        }

        let summaryPayload = this.computeSourcemapValidationSummary(files);
        let scopeLabel = this.activeFilesSessionId ? `Session ${this.shortId(this.activeFilesSessionId)}` : 'Visible scope';
        if (this.activeFilesSessionId) {
            try {
                const response = await axios.get(
                    `${this.apiBase}/api/sessions/${this.activeFilesSessionId}/sourcemap-validation`,
                    { params: { dedupe: true } }
                );
                if (response?.data?.summary) {
                    summaryPayload = response.data.summary;
                    scopeLabel = `Session ${this.shortId(this.activeFilesSessionId)}`;
                }
            } catch (_error) {
                // Keep computed fallback payload.
            }
        }

        const denominators = summaryPayload.denominators || {};
        const counts = summaryPayload.counts || {};
        const rates = summaryPayload.rates || {};
        const reasons = summaryPayload.failure_reasons || {};
        const reasonText = Object.entries(reasons)
            .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))
            .slice(0, 4)
            .map(([name, count]) => `${name}:${count}`)
            .join(', ');

        const totalJs = Number(denominators.total_js || 0);
        const candidates = Number(denominators.map_candidates || 0);
        const fetched = Number(denominators.map_fetched || 0);
        const jsonChecked = Number(denominators.json_checked || 0);
        const processed = Number(counts.processed || 0);
        const failed = Number(counts.failed || 0);
        const noCandidate = Number(counts.no_map_candidate || 0);
        const jsonValid = Number(counts.json_valid || 0);

        node.classList.remove('d-none');
        node.innerHTML = `
            <div class="sourcemap-validation-title">
                <i class="fas fa-map-location-dot me-1"></i>SourceMap Validation Coverage (${this.escapeHtml(scopeLabel)})
            </div>
            <div class="sourcemap-validation-badges">
                <span class="badge bg-dark">Total JS ${totalJs}</span>
                <span class="badge bg-info text-dark">Candidates ${candidates}/${totalJs}</span>
                <span class="badge bg-primary">Fetched ${fetched}/${candidates}</span>
                <span class="badge bg-success">Processed ${processed}/${candidates}</span>
                <span class="badge bg-warning text-dark">JSON valid ${jsonValid}/${jsonChecked}</span>
                <span class="badge bg-secondary">No map ${noCandidate}</span>
                <span class="badge bg-danger">Failed ${failed}</span>
                <span class="badge bg-light text-dark border border-secondary">Candidate rate ${Number(rates.candidatePctOfJs || 0).toFixed(1)}%</span>
                <span class="badge bg-light text-dark border border-secondary">Fetch rate ${Number(rates.fetchPctOfCandidates || 0).toFixed(1)}%</span>
                <span class="badge bg-light text-dark border border-secondary">Process rate ${Number(rates.processPctOfCandidates || 0).toFixed(1)}%</span>
            </div>
            <div class="sourcemap-validation-reasons">
                ${reasonText
                    ? `<strong>Top failure reasons:</strong> ${this.escapeHtml(reasonText)}`
                    : '<strong>Top failure reasons:</strong> none'}
            </div>
        `;
    }

    renderSourcemapLifecycleLine(sourceMap) {
        const lifecycle = this.getSourcemapValidationState(sourceMap);
        if (!sourceMap || !lifecycle.detected) {
            return '<div class="sourcemap-lifecycle-line"><strong>Map lifecycle:</strong> not detected</div>';
        }

        const parts = [
            `detected:${lifecycle.detected ? 'yes' : 'no'}`,
            `fetched:${lifecycle.fetched ? 'yes' : 'no'}`,
            `json:${lifecycle.json_valid === true ? 'valid' : lifecycle.json_valid === false ? 'invalid' : 'unknown'}`,
            `processed:${lifecycle.processed ? 'yes' : 'no'}`,
        ];
        if (lifecycle.http_status) parts.push(`http:${lifecycle.http_status}`);
        if (lifecycle.failure_class) parts.push(`reason:${lifecycle.failure_class}`);
        return `<div class="sourcemap-lifecycle-line"><strong>Map lifecycle:</strong> ${this.escapeHtml(parts.join(' • '))}</div>`;
    }

    renderSourcemapStatusBadge(sourceMap) {
        if (!sourceMap) {
            return '<span class="badge bg-light text-dark me-2" title="No sourcemap information available"><i class="fas fa-map me-1"></i>None</span>';
        }

        const status = sourceMap.processingStatus?.toLowerCase() || 'unknown';
        const detectedUrl = sourceMap.detectedMapUrl;
        const error = sourceMap.processingError;
        
        // Determine badge based on processing status
        if (status === 'completed') {
            const fileCount = sourceMap.reconstructedFilesCount || 0;
            const title = `Sourcemap processed successfully. ${fileCount} files reconstructed.`;
            return `<span class="badge bg-success me-2" title="${this.escapeHtml(title)}"><i class="fas fa-map me-1"></i>Processed</span>`;
        }

        if (status === 'completed_limited') {
            const fileCount = sourceMap.reconstructedFilesCount || 0;
            const detail = error || `Sourcemap processed with limits. ${fileCount} reconstructed files retained.`;
            return `<span class="badge bg-warning text-dark me-2" title="${this.escapeHtml(detail)}"><i class="fas fa-map me-1"></i>Processed (limited)</span>`;
        }
        
        if (status === 'failed') {
            const title = error ? `Sourcemap processing failed: ${error}` : 'Sourcemap processing failed';
            return `<span class="badge bg-danger me-2" title="${this.escapeHtml(title)}"><i class="fas fa-map me-1"></i>Failed</span>`;
        }
        
        if (status === 'processing') {
            const title = 'Sourcemap is being processed...';
            return `<span class="badge bg-warning text-dark me-2" title="${title}"><i class="fas fa-spinner fa-spin me-1"></i>Processing</span>`;
        }
        
        if (status === 'pending' || detectedUrl) {
            const title = detectedUrl ? `Sourcemap detected: ${detectedUrl}` : 'Sourcemap detected but not yet processed';
            return `<span class="badge bg-info me-2" title="${this.escapeHtml(title)}"><i class="fas fa-map me-1"></i>Detected</span>`;
        }
        
        // Unknown status
        return '<span class="badge bg-secondary me-2" title="Sourcemap status unknown"><i class="fas fa-map me-1"></i>Unknown</span>';
    }

    renderReconstructedSourcesButton(sourceMap) {
        if (!sourceMap) {
            return '';
        }
        
        const status = sourceMap.processingStatus?.toLowerCase() || 'unknown';
        const fileCount = sourceMap.reconstructedFilesCount || 0;
        
        // Only show button if sourcemap processing was successful and files were reconstructed
        if ((status === 'completed' || status === 'completed_limited') && fileCount > 0) {
            return `<button class="btn btn-outline-secondary btn-sm ms-2" onclick="dashboard.showReconstructedSources('${sourceMap.fileId || sourceMap.id}')">
                <i class="fas fa-code-branch me-1"></i>View Sources (${fileCount})
            </button>`;
        }
        
        return '';
    }

    collectAnalysisOptions() {
        const includeSourcemap = document.getElementById('include-sourcemap');
        const resolveUrls = document.getElementById('resolve-urls');
        const useRepEndpoints = document.getElementById('use-rep-endpoints');
        const useRepSecrets = document.getElementById('use-rep-secrets');
        const useJSluiceEndpoints = document.getElementById('use-jsluice-endpoints');
        const useJSluiceSecrets = document.getElementById('use-jsluice-secrets');
        return {
            include_sourcemap: includeSourcemap ? includeSourcemap.checked : true,
            resolve_urls: resolveUrls ? resolveUrls.checked : true,
            use_rep_endpoints: useRepEndpoints ? useRepEndpoints.checked : true,
            use_rep_secrets: useRepSecrets ? useRepSecrets.checked : true,
            use_jsluice_endpoints: useJSluiceEndpoints ? useJSluiceEndpoints.checked : false,
            use_jsluice_secrets: useJSluiceSecrets ? useJSluiceSecrets.checked : false
        };
    }

    async showReconstructedSources(fileId) {
        if (!fileId) {
            this.showAlert('Invalid file ID for reconstructed sources', 'danger');
            return;
        }

        const modal = new bootstrap.Modal(document.getElementById('reconstructedSourcesModal'));
        const loadingDiv = document.getElementById('reconstructed-sources-loading');
        const errorDiv = document.getElementById('reconstructed-sources-error');
        const contentDiv = document.getElementById('reconstructed-sources-content');

        // Reset modal content
        loadingDiv.style.display = 'block';
        errorDiv.style.display = 'none';
        contentDiv.innerHTML = '';

        modal.show();

        try {
            const response = await axios.get(`${this.apiBase}/api/files/${fileId}/reconstructed-sources`);
            const data = response.data;

            loadingDiv.style.display = 'none';

            if (!data.files || data.files.length === 0) {
                contentDiv.innerHTML = '<div class="alert alert-info"><i class="fas fa-info-circle"></i> No reconstructed source files found.</div>';
                return;
            }

            contentDiv.innerHTML = this.renderReconstructedSourcesContent(data);
        } catch (error) {
            loadingDiv.style.display = 'none';
            errorDiv.style.display = 'block';
            
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            document.getElementById('reconstructed-sources-error-msg').textContent = message;
        }
    }

    async showSessionSummary(sessionId, encodedName = '') {
        if (!sessionId) {
            this.showAlert('Invalid session id for summary view.', 'warning');
            return;
        }

        const modalElement = document.getElementById('sessionSummaryModal');
        if (!modalElement) {
            this.showAlert('Session summary modal is not available.', 'danger');
            return;
        }

        const modal = new bootstrap.Modal(modalElement);
        const loadingNode = document.getElementById('session-summary-loading');
        const errorNode = document.getElementById('session-summary-error');
        const contentNode = document.getElementById('session-summary-content');
        const errorMessageNode = document.getElementById('session-summary-error-msg');
        const titleNode = document.getElementById('session-summary-title');

        const decodedName = encodedName ? decodeURIComponent(encodedName) : `Session ${this.shortId(sessionId)}`;
        if (titleNode) {
            titleNode.innerHTML = `<i class="fas fa-clipboard-list me-2"></i>Session Analysis Summary - ${this.escapeHtml(decodedName)}`;
        }
        if (loadingNode) loadingNode.style.display = 'block';
        if (errorNode) errorNode.style.display = 'none';
        if (contentNode) contentNode.innerHTML = '';

        modal.show();

        try {
            const response = await axios.get(`${this.apiBase}/api/sessions/${sessionId}/comprehensive-analysis`);
            const data = response.data || {};
            if (loadingNode) loadingNode.style.display = 'none';
            if (contentNode) {
                contentNode.innerHTML = this.renderSessionSummaryContent(data);
            }
        } catch (error) {
            const detail = error?.response?.data?.detail;
            const message = typeof detail === 'string' ? detail : (detail?.message || error.message);
            if (loadingNode) loadingNode.style.display = 'none';
            if (errorNode) errorNode.style.display = 'block';
            if (errorMessageNode) errorMessageNode.textContent = message || 'Failed to load session summary.';
        }
    }

    renderSessionSummaryContent(data) {
        const stats = data?.stats || {};
        const analysis = data?.analysis || {};
        const endpoints = Array.isArray(analysis.endpoints) ? analysis.endpoints : [];
        const secrets = Array.isArray(analysis.secrets) ? analysis.secrets : [];
        const endpointRows = endpoints.map((endpoint) => {
            const value = endpoint?.url || endpoint?.endpoint || endpoint?.value || 'Unknown endpoint';
            const type = endpoint?.type || 'unknown';
            const source = this.renderLocation(endpoint) || 'Unavailable';
            return `
                <tr>
                    <td class="summary-wrap">${this.escapeHtml(value)}</td>
                    <td>${this.escapeHtml(type)}</td>
                    <td class="summary-wrap"><code>${this.escapeHtml(source)}</code></td>
                </tr>
            `;
        }).join('');
        const secretRows = secrets.map((secret) => {
            const type = secret?.type || secret?.rule || 'secret';
            const value = secret?.value || secret?.match || 'redacted';
            const source = this.renderLocation(secret) || 'Unavailable';
            return `
                <tr>
                    <td>${this.escapeHtml(type)}</td>
                    <td class="summary-wrap"><code>${this.escapeHtml(String(value).slice(0, 180))}${String(value).length > 180 ? '...' : ''}</code></td>
                    <td class="summary-wrap"><code>${this.escapeHtml(source)}</code></td>
                </tr>
            `;
        }).join('');

        return `
            <div class="mb-3">
                <span class="badge bg-primary me-2">Files ${Number(stats.total_files) || 0}</span>
                <span class="badge bg-success me-2">Endpoints ${endpoints.length}</span>
                <span class="badge bg-danger me-2">Secrets ${secrets.length}</span>
                <span class="badge bg-warning text-dark me-2">Dependencies ${Number(stats.total_dependencies) || 0}</span>
            </div>

            <h6><i class="fas fa-globe me-2"></i>Endpoints</h6>
            ${endpointRows ? `
                <div class="table-responsive mb-4">
                    <table class="table table-sm table-striped">
                        <thead>
                            <tr><th>Endpoint</th><th>Type</th><th>Source (file:line)</th></tr>
                        </thead>
                        <tbody>${endpointRows}</tbody>
                    </table>
                </div>
            ` : '<div class="alert alert-light border">No endpoints found for this session.</div>'}

            <h6><i class="fas fa-key me-2"></i>Secrets</h6>
            ${secretRows ? `
                <div class="table-responsive">
                    <table class="table table-sm table-striped">
                        <thead>
                            <tr><th>Type</th><th>Value</th><th>Source (file:line)</th></tr>
                        </thead>
                        <tbody>${secretRows}</tbody>
                    </table>
                </div>
            ` : '<div class="alert alert-light border">No secrets found for this session.</div>'}
        `;
    }

    renderReconstructedSourcesContent(data) {
        const { files, stats } = data;
        
        // Summary section
        let content = `
            <div class="mb-4">
                <h6><i class="fas fa-chart-bar me-2"></i>Summary</h6>
                <div class="row">
                    <div class="col-md-3">
                        <div class="text-center p-3 border rounded">
                            <div class="h5 text-primary mb-1">${stats.totalFiles || 0}</div>
                            <div class="text-muted small">Total Files</div>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="text-center p-3 border rounded">
                            <div class="h5 text-success mb-1">${stats.jsFiles || 0}</div>
                            <div class="text-muted small">JavaScript</div>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="text-center p-3 border rounded">
                            <div class="h5 text-info mb-1">${stats.otherFiles || 0}</div>
                            <div class="text-muted small">Other</div>
                        </div>
                    </div>
                    <div class="col-md-3">
                        <div class="text-center p-3 border rounded">
                            <div class="h5 text-secondary mb-1">${this.formatFileSize(stats.totalSize || 0)}</div>
                            <div class="text-muted small">Total Size</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <h6><i class="fas fa-code-branch me-2"></i>Reconstructed Files</h6>
            <div class="table-responsive">
                <table class="table table-sm table-hover">
                    <thead>
                        <tr>
                            <th>File Path</th>
                            <th>Type</th>
                            <th>Size</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        files.forEach((file, index) => {
            const typeIcon = file.type === 'javascript' ? 'fas fa-file-code text-warning' : 'fas fa-file text-secondary';
            content += `
                <tr>
                    <td>
                        <i class="${typeIcon} me-2"></i>
                        <span class="font-monospace">${this.escapeHtml(file.path)}</span>
                        ${file.originalPath && file.originalPath !== file.path ? 
                            `<br><small class="text-muted">Original: ${this.escapeHtml(file.originalPath)}</small>` : ''
                        }
                    </td>
                    <td><span class="badge bg-secondary">${this.escapeHtml(file.type)}</span></td>
                    <td>${this.formatFileSize(file.size)}</td>
                    <td>
                        <button class="btn btn-outline-primary btn-sm" onclick="dashboard.previewReconstructedFile(${index})">
                            <i class="fas fa-eye me-1"></i>Preview
                        </button>
                    </td>
                </tr>
            `;
        });

        content += `
                    </tbody>
                </table>
            </div>
            
            <div id="file-preview-section" style="display: none;">
                <hr>
                <h6><i class="fas fa-eye me-2"></i>File Preview</h6>
                <div id="file-preview-content"></div>
            </div>
        `;

        // Store files data for preview functionality  
        this.currentReconstructedFiles = files;

        return content;
    }

    previewReconstructedFile(index) {
        if (!this.currentReconstructedFiles || index < 0 || index >= this.currentReconstructedFiles.length) {
            this.showAlert('Invalid file index', 'danger');
            return;
        }

        const file = this.currentReconstructedFiles[index];
        const previewSection = document.getElementById('file-preview-section');
        const previewContent = document.getElementById('file-preview-content');

        if (!previewSection || !previewContent) {
            this.showAlert('Preview elements not found', 'danger');
            return;
        }

        // Limit preview content for performance
        const maxPreviewLength = 5000;
        let content = file.content || '';
        let truncated = false;

        if (content.length > maxPreviewLength) {
            content = content.substring(0, maxPreviewLength);
            truncated = true;
        }

        previewContent.innerHTML = `
            <div class="card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${this.escapeHtml(file.path)}</strong>
                        <span class="badge bg-secondary ms-2">${this.formatFileSize(file.size)}</span>
                    </div>
                    <button class="btn btn-outline-secondary btn-sm" onclick="document.getElementById('file-preview-section').style.display='none'">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="card-body">
                    ${truncated ? '<div class="alert alert-info mb-3"><i class="fas fa-info-circle"></i> Content truncated for display performance.</div>' : ''}
                    <pre class="bg-light p-3" style="max-height: 400px; overflow-y: auto;"><code>${this.escapeHtml(content)}</code></pre>
                </div>
            </div>
        `;

        previewSection.style.display = 'block';
        
        // Scroll to preview section
        previewSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

// Global functions for onclick handlers
function showAnalysisTab() {
    window.dashboard.switchTab('analysis');
}

function showFilesTab() {
    window.dashboard.switchTab('files');
}

function showSessionsTab() {
    window.dashboard.switchTab('sessions');
}

function checkAPIStatus() {
    window.dashboard.checkAPIStatus();
}

function refreshFiles() {
    window.dashboard.loadFiles();
}

function clearSessionFilesFilter() {
    window.dashboard.clearFilesSessionFilter();
}

function exportResults() {
    window.dashboard.exportResults();
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new SecurityDashboard();
});

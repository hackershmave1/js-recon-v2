document.addEventListener('DOMContentLoaded', async () => {
  const useDomainScope = document.getElementById('useDomainScope');
  const domainScopes = document.getElementById('domainScopes');
  const useLocalApi = document.getElementById('useLocalApi');
  const apiEndpoint = document.getElementById('apiEndpoint');
  const autoStart = document.getElementById('autoStart');
  const performAnalysisOnUpload = document.getElementById('performAnalysisOnUpload');
  const captureSourceMaps = document.getElementById('captureSourceMaps');
  const allowSourceMapFallback = document.getElementById('allowSourceMapFallback');
  const captureAuthContext = document.getElementById('captureAuthContext');
  const authContextDomains = document.getElementById('authContextDomains');
  const resolveDependencies = document.getElementById('resolveDependencies');
  const importRepPlusSignals = document.getElementById('importRepPlusSignals');
  const repPlusExtensionId = document.getElementById('repPlusExtensionId');
  const exportIncludeContent = document.getElementById('exportIncludeContent');
  const saveBtn = document.getElementById('save');
  const resetBtn = document.getElementById('reset');
  const status = document.getElementById('status');
  let confirmPending = false;
  let confirmPendingTimer = null;

  async function loadSettings() {
    const result = await chrome.storage.local.get([
      'domainScopes',
      'useDomainScope',
      'apiEndpoint',
      'useLocalApi',
      'autoStart',
      'performAnalysisOnUpload',
      'captureSourceMaps',
      'allowSourceMapFallback',
      'captureAuthContext',
      'authContextDomains',
      'resolveDependencies',
      'importRepPlusSignals',
      'repPlusExtensionId',
      'exportIncludeContent'
    ]);

    useDomainScope.checked = result.useDomainScope || false;
    domainScopes.value = (result.domainScopes || []).join('\n');
    useLocalApi.checked = true;
    useLocalApi.disabled = true;
    apiEndpoint.value = result.apiEndpoint || 'http://localhost:3000/api/save-files';
    autoStart.checked = result.autoStart || false;
    performAnalysisOnUpload.checked = result.performAnalysisOnUpload === true;
    captureSourceMaps.checked = result.captureSourceMaps !== false;
    allowSourceMapFallback.checked = result.allowSourceMapFallback || false;
    captureAuthContext.checked = result.captureAuthContext !== false;
    authContextDomains.value = (result.authContextDomains || []).join('\n');
    resolveDependencies.checked = result.resolveDependencies !== false;
    importRepPlusSignals.checked = result.importRepPlusSignals === true;
    repPlusExtensionId.value = result.repPlusExtensionId || '';
    exportIncludeContent.checked = result.exportIncludeContent === true;
  }

  async function saveSettings() {
    const settings = {
      useDomainScope: useDomainScope.checked,
      domainScopes: domainScopes.value.split('\n').filter(d => d.trim()),
      useLocalApi: true,
      apiEndpoint: apiEndpoint.value.trim(),
      autoStart: autoStart.checked,
      performAnalysisOnUpload: performAnalysisOnUpload.checked,
      captureSourceMaps: captureSourceMaps.checked,
      allowSourceMapFallback: allowSourceMapFallback.checked,
      captureAuthContext: captureAuthContext.checked,
      authContextDomains: authContextDomains.value.split('\n').map(d => d.trim()).filter(Boolean),
      resolveDependencies: resolveDependencies.checked,
      importRepPlusSignals: importRepPlusSignals.checked,
      repPlusExtensionId: repPlusExtensionId.value.trim(),
      exportIncludeContent: exportIncludeContent.checked
    };

    try {
      await chrome.storage.local.set(settings);
      await chrome.runtime.sendMessage({
        action: 'updateSettings',
        settings: settings
      });

      showStatus('Settings saved successfully!', 'success');
    } catch (error) {
      showStatus('Failed to save settings: ' + error.message, 'error');
    }
  }

  function resetResetButtonState() {
    confirmPending = false;
    resetBtn.textContent = 'Reset to Defaults';
    if (confirmPendingTimer) {
      clearTimeout(confirmPendingTimer);
      confirmPendingTimer = null;
    }
  }

  function applyDefaults() {
    useDomainScope.checked = false;
    domainScopes.value = '';
    useLocalApi.checked = true;
    apiEndpoint.value = 'http://localhost:3000/api/save-files';
    autoStart.checked = false;
    performAnalysisOnUpload.checked = false;
    captureSourceMaps.checked = true;
    allowSourceMapFallback.checked = false;
    captureAuthContext.checked = true;
    authContextDomains.value = '';
    resolveDependencies.checked = true;
    importRepPlusSignals.checked = false;
    repPlusExtensionId.value = '';
    exportIncludeContent.checked = false;
  }

  function resetSettings() {
    if (!confirmPending) {
      confirmPending = true;
      resetBtn.textContent = 'Click again to confirm reset';
      confirmPendingTimer = setTimeout(resetResetButtonState, 3000);
      return;
    }

    resetResetButtonState();
    applyDefaults();
    saveSettings();
  }

  function showStatus(message, type) {
    status.textContent = message;
    status.className = type;
    status.style.display = 'block';
    
    setTimeout(() => {
      status.style.display = 'none';
    }, 3000);
  }

  saveBtn.addEventListener('click', saveSettings);
  resetBtn.addEventListener('click', resetSettings);

  await loadSettings();
});

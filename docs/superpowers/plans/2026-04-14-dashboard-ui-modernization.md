# Dashboard UI Modernization — Apple-Grade Dark Theme

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the JS Security Extractor web dashboard from a generic Bootstrap 5 light theme into a precision-crafted dark security intelligence tool with Apple-quality aesthetics — dark glass panels, indigo accent, Inter typography, pulse status dots, spring animations, and a macOS-style sidebar nav rail.

**Architecture:** Additive CSS override strategy — preserve Bootstrap's structural grid, modal JS, and form components. Override all visual tokens via CSS custom properties. Targeted HTML edits replace structural elements (navbar → slim titlebar, sidebar buttons → nav rail, stat boxes → bento grid). Zero changes to backend routes, API, or Python code.

**Tech Stack:** Jinja2 HTML template, Bootstrap 5.1.3 (structural only — visuals fully overridden), Inter + JetBrains Mono (Google Fonts), vanilla JS for animation triggers, FastAPI/Uvicorn dev server

**Dev server:** `cd api && uv run uvicorn app.main:app --reload --port 3000` → http://localhost:3000

---

## File Map

| File | Action | Scope |
|---|---|---|
| `api/app/templates/dashboard.html` | Modify | `<head>` meta/fonts, navbar → titlebar, sidebar structure, stat tiles, status bar |
| `api/app/static/dashboard.css` | Rewrite | All rules replaced/extended with dark design system |
| `api/app/static/dashboard.js` | Modify | API status dot update, toast show/hide, stagger class, stats status bar |

---

## Task 1: Dark Design Token Foundation

Replace the 7-token `:root` block with the complete dark design system and load Google Fonts.

**Files:**
- Modify: `api/app/static/dashboard.css:1-12`

- [ ] **Step 1: Back up the CSS file**

```bash
cp api/app/static/dashboard.css api/app/static/dashboard.css.bak
```

- [ ] **Step 2: Replace the entire `:root` block (lines 1–12)**

Replace:
```css
/* JavaScript Security Extractor Dashboard Styles */

:root {
    --primary-color: #0d6efd;
    --success-color: #198754;
    --danger-color: #dc3545;
    --warning-color: #fd7e14;
    --info-color: #0dcaf0;
    --dark-color: #212529;
    --light-gray: #f8f9fa;
    --border-color: #dee2e6;
}
```

With:
```css
/* JS Security Extractor — Apple-grade dark design system */

@import url('https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,300;0,14..32,400;0,14..32,500;0,14..32,600;0,14..32,700;1,14..32,400&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  /* Backgrounds — layered depth */
  --bg-base:        #0D0E11;
  --bg-surface:     #13151A;
  --bg-elevated:    #1C1E26;
  --bg-overlay:     rgba(255,255,255,0.05);

  /* Borders */
  --border-subtle:  rgba(255,255,255,0.06);
  --border-default: rgba(255,255,255,0.10);
  --border-strong:  rgba(255,255,255,0.18);

  /* Text hierarchy */
  --text-primary:   #F2F2F7;
  --text-secondary: #8E8E93;
  --text-tertiary:  #48484A;
  --text-mono:      #A8DAFF;

  /* Accent — Apple system indigo */
  --accent:         #5E6AD2;
  --accent-glow:    rgba(94,106,210,0.20);
  --accent-subtle:  rgba(94,106,210,0.12);

  /* Severity — iOS semantic colors */
  --severity-critical: #FF3B30;
  --severity-high:     #FF9F0A;
  --severity-medium:   #FFD60A;
  --severity-low:      #30D158;
  --severity-info:     #5E6AD2;

  /* Status dot colors */
  --status-live:       #30D158;
  --status-processing: #FF9F0A;
  --status-error:      #FF3B30;
  --status-pending:    #48484A;

  /* Typography */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;

  /* Animation */
  --ease-spring: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in:     cubic-bezier(0.4, 0, 1, 1);
  --dur-fast:    150ms;
  --dur-normal:  250ms;
  --dur-enter:   300ms;

  /* Bootstrap override tokens — keeps existing HTML class names working */
  --bs-body-bg:      #0D0E11;
  --bs-body-color:   #F2F2F7;
  --bs-border-color: rgba(255,255,255,0.10);
  --bs-primary:      #5E6AD2;
  --bs-success:      #30D158;
  --bs-danger:       #FF3B30;
  --bs-warning:      #FF9F0A;
  --bs-info:         #A8DAFF;
  --bs-secondary:    #48484A;

  /* Legacy alias tokens — preserves any inline var() references in JS */
  --primary-color: var(--accent);
  --success-color: var(--severity-low);
  --danger-color:  var(--severity-critical);
  --warning-color: var(--severity-high);
  --info-color:    var(--text-mono);
  --dark-color:    var(--bg-elevated);
  --light-gray:    var(--bg-surface);
  --border-color:  var(--border-default);
}
```

- [ ] **Step 3: Start dev server and verify fonts load**

```bash
cd api && uv run uvicorn app.main:app --reload --port 3000
```

Open http://localhost:3000. DevTools → Network → filter "fonts.goog". Confirm `Inter` and `JetBrains+Mono` appear with status 200. The page will still look light — body bg is set in Task 2.

- [ ] **Step 4: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark design token system and Google Fonts (Inter + JetBrains Mono)"
```

---

## Task 2: Dark Base Styles + Bootstrap Global Overrides

Apply dark body, color-scheme, custom scrollbar, and override Bootstrap form/modal/tab/alert defaults.

**Files:**
- Modify: `api/app/static/dashboard.css:14-17` (body block)

- [ ] **Step 1: Replace body block with full base system**

Replace:
```css
body {
    background-color: var(--light-gray);
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}
```

With:
```css
*, *::before, *::after { box-sizing: border-box; }

html { color-scheme: dark; }

body {
  background-color: var(--bg-base);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  margin: 0;
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-default); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-strong); }

/* Text selection */
::selection { background: var(--accent-subtle); color: var(--text-primary); }

/* Bootstrap form control overrides */
.form-control, .form-select {
  background-color: var(--bg-elevated);
  border-color: var(--border-default);
  color: var(--text-primary);
}
.form-control:focus, .form-select:focus {
  background-color: var(--bg-elevated);
  border-color: var(--accent);
  color: var(--text-primary);
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.form-control::placeholder { color: var(--text-tertiary); }
.form-select option { background: var(--bg-elevated); color: var(--text-primary); }

/* Bootstrap modal overrides */
.modal-content {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 16px;
  color: var(--text-primary);
}
.modal-header { border-bottom: 1px solid var(--border-subtle); padding: 20px 24px 16px; }
.modal-body   { padding: 20px 24px; }
.modal-footer { border-top: 1px solid var(--border-subtle); padding: 16px 24px; }
.modal-title  { color: var(--text-primary); font-weight: 600; }
.modal-backdrop { background-color: rgba(0,0,0,0.7); }
.btn-close { filter: invert(1) opacity(0.5); }
.btn-close:hover { filter: invert(1) opacity(0.9); }

/* Bootstrap nav-tabs overrides */
.nav-tabs { border-bottom: 1px solid var(--border-subtle); }
.nav-tabs .nav-link {
  color: var(--text-secondary);
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 8px 16px;
  font-size: 0.875rem;
  font-weight: 500;
  transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease;
}
.nav-tabs .nav-link:hover { color: var(--text-primary); background: none; border-bottom-color: var(--border-default); }
.nav-tabs .nav-link.active { color: var(--accent); background: none; border-bottom: 2px solid var(--accent); }

/* Bootstrap alert overrides */
.alert-light   { background: var(--bg-elevated); border-color: var(--border-default); color: var(--text-secondary); }
.alert-danger  { background: rgba(255,59,48,0.1); border-color: rgba(255,59,48,0.3); color: #FF6B6B; }
.alert-info    { background: rgba(168,218,255,0.1); border-color: rgba(168,218,255,0.3); color: var(--text-mono); }

/* Bootstrap form-check overrides */
.form-check-input { background-color: var(--bg-elevated); border-color: var(--border-strong); }
.form-check-input:checked { background-color: var(--accent); border-color: var(--accent); }
.form-check-label { color: var(--text-primary); font-size: 0.875rem; }

/* Bootstrap hr */
hr { border-color: var(--border-subtle); opacity: 1; }

/* Bootstrap text-muted */
.text-muted { color: var(--text-secondary) !important; }

/* Bootstrap small */
small, .small { color: var(--text-secondary); }
```

- [ ] **Step 2: Verify dark base renders**

Reload http://localhost:3000. Page background should be `#0D0E11` (near-black). Text should be `#F2F2F7` (off-white). Form controls, modals, and tabs should pick up dark overrides.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark base styles, scrollbar, Bootstrap form/modal/tab/alert overrides"
```

---

## Task 3: Slim Frosted Titlebar (replaces Bootstrap navbar)

Replace the Bootstrap `.navbar.bg-dark` with a minimal 48px frosted glass titlebar with a live pulse dot for API status.

**Files:**
- Modify: `api/app/static/dashboard.css` (add after body section)
- Modify: `api/app/templates/dashboard.html:15-29`
- Modify: `api/app/static/dashboard.js` (add `updateApiStatusIndicator` helper)

- [ ] **Step 1: Add titlebar CSS after the base styles section**

```css
/* ── Titlebar ─────────────────────────────────────────────── */
.app-titlebar {
  position: sticky;
  top: 0;
  z-index: 100;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: rgba(13,14,17,0.85);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-bottom: 1px solid var(--border-subtle);
  flex-shrink: 0;
}
.titlebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}
.titlebar-brand svg { color: var(--accent); flex-shrink: 0; }
.titlebar-right { display: flex; align-items: center; gap: 12px; }
.api-status-dot {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-secondary);
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-live {
  background: var(--status-live);
  box-shadow: 0 0 0 0 rgba(48,209,88,0.6);
  animation: pulse-live 2s infinite;
}
.dot-error   { background: var(--status-error); }
.dot-pending { background: var(--status-pending); }
@keyframes pulse-live {
  0%   { box-shadow: 0 0 0 0   rgba(48,209,88,0.6); }
  70%  { box-shadow: 0 0 0 5px rgba(48,209,88,0); }
  100% { box-shadow: 0 0 0 0   rgba(48,209,88,0); }
}
.titlebar-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 7px;
  border: 1px solid var(--border-default);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: 0.75rem;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--dur-fast) ease, color var(--dur-fast) ease;
}
.titlebar-btn:hover { background: var(--bg-overlay); color: var(--text-primary); }
```

- [ ] **Step 2: Replace Bootstrap navbar HTML (lines 15–29 of dashboard.html)**

Replace:
```html
        <!-- Header -->
        <nav class="navbar navbar-dark bg-dark mb-4">
            <div class="container-fluid">
                <span class="navbar-brand mb-0 h1">
                    <i class="fas fa-shield-alt"></i> JavaScript Security Extractor
                </span>
                <div class="navbar-nav flex-row">
                    <span class="nav-item me-3">
                        <span class="badge bg-success" id="api-status">API Connected</span>
                    </span>
                    <button class="btn btn-outline-light btn-sm" onclick="checkAPIStatus()">
                        <i class="fas fa-sync-alt"></i> Refresh
                    </button>
                </div>
            </div>
        </nav>
```

With:
```html
        <!-- Titlebar -->
        <header class="app-titlebar">
            <div class="titlebar-brand">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                JS Security Extractor
            </div>
            <div class="titlebar-right">
                <div class="api-status-dot" id="api-status-wrapper">
                    <span class="dot dot-pending" id="api-status-dot"></span>
                    <span id="api-status-text">Connecting…</span>
                </div>
                <button class="titlebar-btn" onclick="checkAPIStatus()" aria-label="Refresh API status">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                    Refresh
                </button>
            </div>
        </header>
```

- [ ] **Step 3: Find and update API status references in dashboard.js**

```bash
grep -n "api-status\|apiStatus\|API Connected\|API Disconnected" api/app/static/dashboard.js | head -20
```

In the `checkAPIStatus()` method, find where the badge element is updated. Add this helper method to the `SecurityDashboard` class (place near the `checkAPIStatus` method):

```js
updateApiStatusIndicator(connected) {
    const dot  = document.getElementById('api-status-dot');
    const text = document.getElementById('api-status-text');
    // Legacy badge fallback (no-op if element removed)
    const badge = document.getElementById('api-status');
    if (dot) dot.className = connected ? 'dot dot-live' : 'dot dot-error';
    if (text) text.textContent = connected ? 'API Connected' : 'API Disconnected';
    if (badge) badge.textContent = connected ? 'API Connected' : 'API Disconnected';
}
```

Then call `this.updateApiStatusIndicator(true)` on success and `this.updateApiStatusIndicator(false)` on failure inside `checkAPIStatus()`.

- [ ] **Step 4: Verify titlebar**

Reload http://localhost:3000. A 48px frosted glass bar should appear at the top. Shield SVG in indigo. Green pulsing dot once API responds. No Bootstrap dark navbar visible.

- [ ] **Step 5: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css api/app/static/dashboard.js
git commit -m "feat(ui): frosted glass titlebar with pulsing API status dot"
```

---

## Task 4: App Shell Layout

Convert the Bootstrap `.container-fluid > .row` into an `app-shell` flex layout that supports a fixed sidebar and scrollable main area.

**Files:**
- Modify: `api/app/static/dashboard.css`
- Modify: `api/app/templates/dashboard.html` (wrapper structure)

- [ ] **Step 1: Add shell layout CSS**

```css
/* ── App Shell ────────────────────────────────────────────── */
.app-shell {
  display: flex;
  height: calc(100vh - 48px);   /* viewport minus titlebar */
  overflow: hidden;
}
.app-sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 16px 8px;
}
.app-main {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  padding-bottom: 52px;   /* clearance for status bar */
  min-width: 0;
}
#main-content-area { width: 100%; }
```

- [ ] **Step 2: Replace outer wrapper HTML**

The outermost `<div class="container-fluid">` (line 13 in original) opens the shell. Replace it and its immediate row child with the app-shell structure.

Replace:
```html
    <div class="container-fluid">
        <!-- Header -->
        <header class="app-titlebar">
          ...
        </header>

        <div class="row">
            <!-- Left Sidebar -->
            <div class="col-md-3">
                ...sidebar content...
            </div>

            <!-- Main Content Area -->
            <div class="col-md-9">
```

With:
```html
    <div class="app-shell">
        <header class="app-titlebar">
          ...titlebar from Task 3 (already there)...
        </header>

        <aside class="app-sidebar" id="app-sidebar">
            <!-- Nav rail populated in Task 5 -->
        </aside>

        <main class="app-main">
            <div id="main-content-area">
```

And the closing tags at the bottom — replace the two closing `</div>` tags that closed `.col-md-9` and `.row` and `.container-fluid` with:

```html
            </div><!-- /#main-content-area -->
        </main>
    </div><!-- /.app-shell -->
```

**Important:** Delete the entire old `.col-md-3` block (the Quick Actions + Statistics card). Their replacements go into `<aside>` in Task 5 and Task 6.

- [ ] **Step 3: Verify layout**

Reload. Page should show: frosted titlebar + sidebar (empty) + main content. No horizontal scroll. Form and table content in main area should not be clipped.

- [ ] **Step 4: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css
git commit -m "feat(ui): app shell flex layout with sidebar and scrollable main area"
```

---

## Task 5: Sidebar Nav Rail

Populate the `<aside class="app-sidebar">` with Lucide SVG icon nav items.

**Files:**
- Modify: `api/app/templates/dashboard.html` (aside content)
- Modify: `api/app/static/dashboard.css`
- Modify: `api/app/static/dashboard.js` (`switchTab` method)

- [ ] **Step 1: Add nav rail CSS**

```css
/* ── Sidebar Nav Rail ─────────────────────────────────────── */
.nav-section-label {
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  padding: 12px 12px 6px;
  display: block;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  border: none;
  background: none;
  width: 100%;
  text-align: left;
  font-family: var(--font-sans);
  transition: background var(--dur-fast) ease, color var(--dur-fast) ease;
}
.nav-item:hover  { background: var(--bg-elevated); color: var(--text-primary); }
.nav-item.active { background: var(--accent-subtle); color: var(--accent); }
.nav-item svg    { width: 16px; height: 16px; flex-shrink: 0; }
.nav-divider     { height: 1px; background: var(--border-subtle); margin: 8px 4px; }
.nav-spacer      { flex: 1; }
```

- [ ] **Step 2: Add nav rail HTML inside `<aside class="app-sidebar">`**

```html
            <span class="nav-section-label">Workspace</span>

            <button class="nav-item active" id="nav-analysis" onclick="showAnalysisTab()" aria-label="New Scan">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                Scan
            </button>

            <button class="nav-item" id="nav-files" onclick="showFilesTab()" aria-label="View Files">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                Files
            </button>

            <button class="nav-item" id="nav-sessions" onclick="showSessionsTab()" aria-label="Sessions">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                Sessions
            </button>

            <div class="nav-divider"></div>

            <!-- Bento stats — populated in Task 6 -->
            <div id="sidebar-stats-container"></div>

            <div class="nav-spacer"></div>
```

- [ ] **Step 3: Update `switchTab` in dashboard.js to sync nav active state**

Find the `switchTab(tab)` method in `dashboard.js`. After its existing show/hide logic, add:

```js
// Sync nav rail active state
document.querySelectorAll('.nav-item[id^="nav-"]').forEach(el => el.classList.remove('active'));
const activeNav = document.getElementById('nav-' + tab);
if (activeNav) activeNav.classList.add('active');
```

- [ ] **Step 4: Verify nav rail**

Reload. Left sidebar shows: "WORKSPACE" label → Scan (indigo active) → Files → Sessions → divider. Click each item: main content switches AND active highlight moves correctly.

- [ ] **Step 5: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css api/app/static/dashboard.js
git commit -m "feat(ui): sidebar nav rail with Lucide SVG icons and active state sync"
```

---

## Task 6: Bento Stat Grid

Replace the 3 identical stacked gradient blue stat-boxes with a 5-tile bento grid in the sidebar.

**Files:**
- Modify: `api/app/templates/dashboard.html` (replace `#sidebar-stats-container` content)
- Modify: `api/app/static/dashboard.css` (replace stat-box block, add bento)
- Modify: `api/app/static/dashboard.js` (`loadStatistics` + add `total-secrets`, `total-sourcemaps`)

- [ ] **Step 1: Replace stat-box CSS block (old lines 19–38) with bento CSS**

Remove:
```css
/* Stat boxes in sidebar */
.stat-box { ... }
.stat-number { ... }
.stat-label { ... }
```

Add:
```css
/* ── Bento Stat Tiles ─────────────────────────────────────── */
.stat-bento {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 8px 4px;
}
.stat-tile {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 10px 8px;
  cursor: pointer;
  transition: background var(--dur-fast) ease, border-color var(--dur-fast) ease;
}
.stat-tile:hover { background: var(--bg-overlay); border-color: var(--border-default); }
.stat-tile.span-full { grid-column: 1 / -1; }
.stat-num {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.stat-lbl {
  font-size: 0.6875rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.stat-tile[data-accent="green"]  .stat-num { color: var(--severity-low); }
.stat-tile[data-accent="red"]    .stat-num { color: var(--severity-critical); }
.stat-tile[data-accent="indigo"] .stat-num { color: var(--accent); }
```

- [ ] **Step 2: Add bento HTML inside `#sidebar-stats-container`**

Replace `<div id="sidebar-stats-container"></div>` with:

```html
            <div class="stat-bento">
                <div class="stat-tile" onclick="showFilesTab()" title="View all files">
                    <div class="stat-num" id="total-files">—</div>
                    <div class="stat-lbl">JS Files</div>
                </div>
                <div class="stat-tile" onclick="showSessionsTab()" title="View sessions">
                    <div class="stat-num" id="total-sessions">—</div>
                    <div class="stat-lbl">Sessions</div>
                </div>
                <div class="stat-tile" data-accent="green" title="Endpoints found">
                    <div class="stat-num" id="total-endpoints">—</div>
                    <div class="stat-lbl">Endpoints</div>
                </div>
                <div class="stat-tile" data-accent="red" title="Secrets detected">
                    <div class="stat-num" id="total-secrets">—</div>
                    <div class="stat-lbl">Secrets</div>
                </div>
                <div class="stat-tile span-full" data-accent="indigo" title="Sourcemaps processed">
                    <div class="stat-num" id="total-sourcemaps">—</div>
                    <div class="stat-lbl">Sourcemaps</div>
                </div>
            </div>
```

- [ ] **Step 3: Verify bento grid renders**

Reload. Sidebar should show a 2-col bento grid with 5 tiles: Files + Sessions (row 1), Endpoints + Secrets (row 2), Sourcemaps spanning full width (row 3). Numbers show "—" until stats load.

- [ ] **Step 4: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css
git commit -m "feat(ui): bento stat grid replaces stacked gradient stat boxes"
```

---

## Task 7: Dark Glass Panels (Bootstrap card override)

Replace the dark gradient `.card-header` with frameless glass panels.

**Files:**
- Modify: `api/app/static/dashboard.css` (card block, old lines 40–58)

- [ ] **Step 1: Replace card CSS**

Replace:
```css
/* Card enhancements */
.card {
    border: none;
    border-radius: 12px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.card-header {
    background: linear-gradient(135deg, var(--dark-color), #495057);
    color: white;
    border-radius: 12px 12px 0 0 !important;
    border-bottom: none;
}
```

With:
```css
/* ── Dark Glass Panels ────────────────────────────────────── */
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 16px;
  box-shadow: none;
  transition: border-color var(--dur-fast) ease;
}
.card:hover { border-color: var(--border-default); }
.card-header {
  background: transparent;
  border-bottom: 1px solid var(--border-subtle);
  border-radius: 16px 16px 0 0;
  padding: 14px 20px;
  color: var(--text-primary);
}
.card-header h5, .card-header h6 {
  font-size: 0.8125rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  margin: 0;
}
.card-body   { padding: 20px; background: transparent; color: var(--text-primary); }
.card-footer { background: transparent; border-top: 1px solid var(--border-subtle); padding: 12px 20px; }
```

- [ ] **Step 2: Verify panel appearance**

Reload. Content panels should be dark (`#13151A`) with hairline borders (no shadow). Panel headers show uppercase small-caps section labels in muted color. No gradient anywhere.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark glass panels replace Bootstrap card gradient headers"
```

---

## Task 8: Dark Buttons

Override all Bootstrap button variants for the dark theme.

**Files:**
- Modify: `api/app/static/dashboard.css` (btn block, old lines 80–89)

- [ ] **Step 1: Replace button CSS**

Replace:
```css
/* Button enhancements */
.btn {
    border-radius: 8px;
    font-weight: 500;
    transition: all 0.2s ease;
}
.btn:hover {
    transform: translateY(-1px);
}
```

With:
```css
/* ── Buttons ──────────────────────────────────────────────── */
.btn {
  border-radius: 8px;
  font-weight: 500;
  font-size: 0.875rem;
  font-family: var(--font-sans);
  transition: background var(--dur-fast) ease, border-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease;
}
.btn:hover { transform: none; }   /* no layout-shifting translateY */

.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-primary:hover { background: #6b77e0; border-color: #6b77e0; color: #fff; }
.btn-primary:focus { box-shadow: 0 0 0 3px var(--accent-glow); }

.btn-success { background: rgba(48,209,88,0.15); border-color: rgba(48,209,88,0.3); color: var(--severity-low); }
.btn-success:hover { background: rgba(48,209,88,0.25); color: var(--severity-low); border-color: rgba(48,209,88,0.4); }

.btn-secondary { background: var(--bg-elevated); border-color: var(--border-default); color: var(--text-secondary); }
.btn-secondary:hover { background: var(--bg-overlay); color: var(--text-primary); }

.btn-info { background: rgba(168,218,255,0.12); border-color: rgba(168,218,255,0.25); color: var(--text-mono); }
.btn-info:hover { background: rgba(168,218,255,0.2); color: var(--text-mono); }

.btn-danger { background: rgba(255,59,48,0.15); border-color: rgba(255,59,48,0.3); color: var(--severity-critical); }
.btn-danger:hover { background: rgba(255,59,48,0.25); color: var(--severity-critical); }

.btn-outline-secondary { border-color: var(--border-default); color: var(--text-secondary); background: transparent; }
.btn-outline-secondary:hover { background: var(--bg-elevated); color: var(--text-primary); border-color: var(--border-strong); }

.btn-outline-primary { border-color: rgba(94,106,210,0.4); color: var(--accent); background: transparent; }
.btn-outline-primary:hover { background: var(--accent-subtle); border-color: var(--accent); color: var(--accent); }

.btn-outline-light { border-color: var(--border-default); color: var(--text-secondary); background: transparent; }
.btn-outline-light:hover { background: var(--bg-elevated); color: var(--text-primary); }

.btn-outline-danger { border-color: rgba(255,59,48,0.35); color: var(--severity-critical); background: transparent; }
.btn-outline-danger:hover { background: rgba(255,59,48,0.1); }

.btn-lg { padding: 12px 20px; font-size: 0.9375rem; border-radius: 10px; }
.btn-sm { padding: 4px 10px; font-size: 0.8125rem; border-radius: 6px; }
```

- [ ] **Step 2: Verify all button variants**

Reload → Scan tab. "Start Analysis" button (btn-success) should be a subtle green ghost. Reload → Sessions tab. "Create New Session" (btn-success) same green ghost. Modals should have correct outline-secondary Cancel and colored confirm buttons.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark button system with correct hover states for all variants"
```

---

## Task 9: Dark Data Tables

Override Bootstrap `.table` with clean borderless dark table styling.

**Files:**
- Modify: `api/app/static/dashboard.css` (file-table block, old lines 308–334)

- [ ] **Step 1: Replace file-table and add global table dark override**

Replace:
```css
/* File table styling */
.file-table { font-size: 0.9rem; }
.file-table th { background: var(--light-gray); border-top: none; font-weight: 600; }
.file-table td { vertical-align: middle; }
.file-url { font-family: monospace; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-size { font-family: monospace; color: #6c757d; }
```

With:
```css
/* ── Data Tables ──────────────────────────────────────────── */
.table { color: var(--text-primary); --bs-table-bg: transparent; --bs-table-border-color: var(--border-subtle); }
.table > :not(caption) > * > * { background-color: transparent; border-color: var(--border-subtle); color: var(--text-primary); }
.table-hover > tbody > tr:hover > * { background-color: var(--bg-elevated); --bs-table-accent-bg: var(--bg-elevated); }
.table-striped > tbody > tr:nth-of-type(odd) > * { --bs-table-accent-bg: transparent; background-color: transparent; }
.table thead th { border-bottom: 1px solid var(--border-default) !important; }

.file-table { font-size: 0.875rem; }
.file-table th {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-secondary);
  background: transparent;
  border-bottom: 1px solid var(--border-subtle);
  padding: 8px 12px;
  white-space: nowrap;
}
.file-table td { vertical-align: middle; padding: 10px 12px; border-bottom: 1px solid var(--border-subtle); }
.file-table tbody tr:last-child td { border-bottom: none; }
.file-url {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-mono);
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.file-size { font-family: var(--font-mono); color: var(--text-secondary); font-size: 0.75rem; }

/* table-sm padding */
.table-sm > :not(caption) > * > * { padding: 6px 12px; }
```

- [ ] **Step 2: Verify tables**

Reload → Files tab. Table should have: dark surface background, uppercase muted column headers, hairline row dividers, monospace blue URL text, elevated bg on hover. No striping (removed).

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark data tables with uppercase headers and monospace URL column"
```

---

## Task 10: Pulse Status Dots + Badge Chips

Replace Bootstrap badge pills with semantic status dots and dark chip-style badges.

**Files:**
- Modify: `api/app/static/dashboard.css` (status-indicator block, old lines 336–347)

- [ ] **Step 1: Replace status-indicator CSS and override Bootstrap `.badge`**

Replace:
```css
/* Status indicators */
.status-indicator { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 8px; }
.status-connected    { background-color: var(--success-color); }
.status-disconnected { background-color: var(--danger-color); }
.status-loading      { background-color: var(--warning-color); }
```

With:
```css
/* ── Status Dots ──────────────────────────────────────────── */
.status-indicator { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.status-connected    { background: var(--status-live); }
.status-disconnected { background: var(--status-error); }
.status-loading      { background: var(--status-processing); animation: pulse-proc 1.2s ease infinite; }
@keyframes pulse-proc {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}

/* ── Badge Chips ──────────────────────────────────────────── */
.badge {
  font-family: var(--font-sans);
  font-size: 0.6875rem;
  font-weight: 500;
  padding: 3px 8px;
  border-radius: 5px;
  letter-spacing: 0.02em;
}
.badge.bg-primary   { background: var(--accent-subtle) !important; color: var(--accent) !important; border: 1px solid rgba(94,106,210,0.25); }
.badge.bg-success   { background: rgba(48,209,88,0.12) !important;  color: var(--severity-low) !important;      border: 1px solid rgba(48,209,88,0.25); }
.badge.bg-danger    { background: rgba(255,59,48,0.12) !important;   color: var(--severity-critical) !important; border: 1px solid rgba(255,59,48,0.25); }
.badge.bg-warning   { background: rgba(255,159,10,0.12) !important;  color: var(--severity-high) !important;     border: 1px solid rgba(255,159,10,0.25); }
.badge.bg-info      { background: rgba(168,218,255,0.12) !important; color: var(--text-mono) !important;         border: 1px solid rgba(168,218,255,0.25); }
.badge.bg-secondary { background: var(--bg-elevated) !important; color: var(--text-secondary) !important; border: 1px solid var(--border-default); }
```

- [ ] **Step 2: Verify badges**

Reload → navigate to a session with analysis results. Endpoint/Secret/Dependency count badges in the results nav-tabs should be dark chip-style with colored borders, not solid Bootstrap pills.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): pulse status dots and dark chip badges replacing Bootstrap pills"
```

---

## Task 11: Dark Secret Display

Replace yellow sticky-note secret styling with threat-appropriate dark cards and `filter:blur()` masking.

**Files:**
- Modify: `api/app/static/dashboard.css` (secret-value block, old lines 189–215)

- [ ] **Step 1: Replace secret CSS**

Replace:
```css
/* Secret styling */
.secret-value { font-family: monospace; background: #fff3cd; border: 1px solid #ffeaa7; padding: 8px; border-radius: 4px; color: #856404; word-break: break-all; position: relative; }
.secret-value.masked { color: transparent; text-shadow: 0 0 8px #856404; }
.secret-toggle { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; color: #856404; cursor: pointer; }
```

With:
```css
/* ── Secrets ──────────────────────────────────────────────── */
.secret-value {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  background: rgba(0,0,0,0.3);
  border: 1px solid var(--border-subtle);
  padding: 8px 36px 8px 10px;
  border-radius: 6px;
  color: var(--text-mono);
  word-break: break-all;
  position: relative;
  transition: filter 200ms ease;
  user-select: none;
}
.secret-value.masked { filter: blur(5px); text-shadow: none; user-select: none; }
.secret-value:not(.masked) { filter: blur(0); user-select: text; }

.secret-toggle {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: color var(--dur-fast) ease;
}
.secret-toggle:hover { color: var(--text-primary); }

/* Secret result items get a left threat border */
.result-item[data-category="secret"] {
  background: rgba(255,59,48,0.05);
  border-color: rgba(255,59,48,0.15);
  border-left: 3px solid var(--severity-critical);
}
```

- [ ] **Step 2: Verify secrets render**

Run an analysis or open a session with secrets. Secret value boxes should be dark with blurred monospace blue text. Reveal button should be small and subtle in the top-right of the value box. Clicking it should un-blur the text with a smooth 200ms transition.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark threat secret cards with blur masking instead of yellow background"
```

---

## Task 12: Terminal Code Viewer

Transform the dashed-border form textarea into a terminal-style dark code viewer.

**Files:**
- Modify: `api/app/static/dashboard.css` (js-content block, old lines 61–78)

- [ ] **Step 1: Replace textarea and code display CSS**

Replace:
```css
/* Textarea enhancements */
#js-content { font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace; font-size: 14px; line-height: 1.5; resize: vertical; border: 2px dashed var(--border-color); transition: border-color 0.3s ease; }
#js-content:focus { border-color: var(--primary-color); border-style: solid; }
#js-content.drag-over { border-color: var(--success-color); background-color: rgba(25, 135, 84, 0.1); }
```

With:
```css
/* ── Terminal Code Viewer ─────────────────────────────────── */
#js-content {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
  resize: vertical;
  background: #0A0A0C;
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  color: var(--text-mono);
  padding: 14px 16px;
  transition: border-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease;
}
#js-content:focus {
  border-color: var(--accent);
  outline: none;
  box-shadow: 0 0 0 3px var(--accent-glow);
}
#js-content.drag-over {
  border-color: var(--severity-low);
  background-color: rgba(48,209,88,0.04);
  box-shadow: 0 0 0 3px rgba(48,209,88,0.12);
}
#js-content::placeholder { color: var(--text-tertiary); }

/* Result context code blocks */
.result-context {
  background: #0A0A0C;
  border-left: 3px solid var(--accent);
  border-radius: 0 6px 6px 0;
  padding: 10px 14px;
  margin-top: 10px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--text-mono);
  overflow-x: auto;
  white-space: pre-wrap;
  line-height: 1.6;
}
.sourcemap-preview {
  font-family: var(--font-mono);
  background: #0A0A0C;
  border: 1px solid var(--border-subtle);
  padding: 12px 14px;
  border-radius: 8px;
  font-size: 0.8rem;
  line-height: 1.6;
  white-space: pre-wrap;
  color: var(--text-mono);
}

/* Generic pre/code blocks */
pre { background: #0A0A0C; border: 1px solid var(--border-subtle); border-radius: 8px; padding: 14px; color: var(--text-mono); font-family: var(--font-mono); font-size: 0.8125rem; }
code { font-family: var(--font-mono); color: var(--text-mono); background: rgba(168,218,255,0.08); padding: 1px 5px; border-radius: 3px; font-size: 0.875em; }
pre code { background: none; padding: 0; }
```

- [ ] **Step 2: Verify code viewer**

Reload → Scan tab → JS content textarea should look like a terminal: `#0A0A0C` background, blue monospace placeholder text, thin border that glows indigo on focus.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): terminal-style dark code viewer with monospace blue text"
```

---

## Task 13: Sourcemap Pipeline Indicator

Add visual pipeline CSS for the sourcemap lifecycle: `detected → fetched → json_valid → processed`.

**Files:**
- Modify: `api/app/static/dashboard.css` (add after sourcemap-file-content block)

- [ ] **Step 1: Add pipeline CSS**

```css
/* ── Sourcemap Pipeline ───────────────────────────────────── */
.pipeline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0;
  margin: 8px 0;
  font-size: 0.75rem;
}
.pipeline-step {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-weight: 500;
  color: var(--text-tertiary);
  white-space: nowrap;
}
.pipeline-step.done   { color: var(--severity-low); }
.pipeline-step.active { color: var(--severity-high); }
.pipeline-step.failed { color: var(--severity-critical); }
.pipeline-step .p-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.pipeline-step.active .p-dot { animation: pulse-proc 1s ease infinite; }
.pipeline-arrow { width: 20px; height: 1px; background: var(--border-subtle); margin: 0 4px; }
.pipeline-step.done ~ .pipeline-arrow { background: rgba(48,209,88,0.35); }

/* Sourcemap validation summary dark */
.sourcemap-validation-summary {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 14px;
}
.sourcemap-validation-title   { color: var(--text-primary); font-size: 0.875rem; font-weight: 600; margin-bottom: 6px; }
.sourcemap-validation-badges  { display: flex; flex-wrap: wrap; gap: 6px; }
.sourcemap-validation-reasons { color: var(--text-secondary); font-size: 0.8rem; margin-top: 8px; overflow-wrap: anywhere; }
.sourcemap-lifecycle-line     { color: var(--text-secondary); font-size: 0.775rem; margin-top: 6px; overflow-wrap: anywhere; }

/* Sourcemap file tree dark */
.sourcemap-file { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: 8px; margin-bottom: 8px; overflow: hidden; transition: border-color var(--dur-fast) ease; }
.sourcemap-file:hover { border-color: var(--border-default); }
.sourcemap-file-header { background: var(--bg-overlay); padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--border-subtle); display: flex; justify-content: space-between; align-items: center; color: var(--text-secondary); font-size: 0.875rem; transition: color var(--dur-fast) ease; }
.sourcemap-file-header:hover { color: var(--text-primary); }
.sourcemap-file-content { padding: 14px; max-height: 400px; overflow-y: auto; }
```

- [ ] **Step 2: Verify sourcemap panels**

Reload → open a session that has sourcemaps → navigate to sourcemap results. Validation summary and file tree should be dark-themed. Lifecycle badges should be dark chips.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): sourcemap pipeline indicator and dark sourcemap panels"
```

---

## Task 14: Slide-Up Progress Toast

Replace the top-right fixed white panel with a bottom-right slide-up dark toast using spring animation.

**Files:**
- Modify: `api/app/static/dashboard.css` (analysis-progress block, old lines 400–423)
- Modify: `api/app/templates/dashboard.html` (add progress bar inside the toast div)
- Modify: `api/app/static/dashboard.js` (replace display:none/block with class-based toggle)

- [ ] **Step 1: Replace analysis-progress CSS**

Replace:
```css
/* Analysis progress */
.analysis-progress { position: fixed; top: 20px; right: 20px; background: white; border: 1px solid var(--border-color); border-radius: 8px; padding: 15px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); z-index: 1050; min-width: 300px; display: none; }
.analysis-progress.show { display: block; animation: slideInRight 0.3s ease-out; }
@keyframes slideInRight { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
```

With:
```css
/* ── Progress Toast ───────────────────────────────────────── */
.analysis-progress {
  position: fixed;
  bottom: 52px;     /* above status bar */
  right: 24px;
  width: 340px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: 16px;
  padding: 16px 18px;
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px var(--border-subtle);
  z-index: 1050;
  transform: translateY(calc(100% + 80px));
  opacity: 0;
  transition:
    transform var(--dur-enter) var(--ease-spring),
    opacity   var(--dur-enter) var(--ease-spring);
  pointer-events: none;
  color: var(--text-primary);
}
.analysis-progress.show {
  transform: translateY(0);
  opacity: 1;
  pointer-events: auto;
}
.toast-progress-track {
  height: 3px;
  background: var(--border-subtle);
  border-radius: 2px;
  overflow: hidden;
  margin-top: 12px;
}
.toast-progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  width: 0%;
  transition: width 400ms var(--ease-spring);
}
```

- [ ] **Step 2: Add progress bar HTML inside the `analysis-progress` div in dashboard.html**

Locate `<div class="analysis-progress" id="analysis-progress">` and ensure it has a progress bar at the end of its children:

```html
<div class="toast-progress-track">
    <div class="toast-progress-fill" id="toast-progress-fill"></div>
</div>
```

- [ ] **Step 3: Find and update toast show/hide in dashboard.js**

```bash
grep -n "analysis-progress\|display.*block\|display.*none" api/app/static/dashboard.js | grep -i "progress" | head -20
```

Replace all patterns like:
```js
progressEl.style.display = 'block';
progressEl.style.display = 'none';
```
With:
```js
progressEl.classList.add('show');
progressEl.classList.remove('show');
```

When progress percentage is computed (in session analysis polling), also drive the fill bar:
```js
const fill = document.getElementById('toast-progress-fill');
if (fill && typeof pct === 'number') fill.style.width = Math.min(pct, 100) + '%';
```

- [ ] **Step 4: Verify toast behavior**

Start a session analysis. Toast should slide up from bottom-right with spring motion. Indigo bar fills left-to-right. On completion, toast slides back down. No white panel flashes from top-right.

- [ ] **Step 5: Commit**

```bash
git add api/app/static/dashboard.css api/app/templates/dashboard.html api/app/static/dashboard.js
git commit -m "feat(ui): slide-up spring progress toast replaces top-right fixed panel"
```

---

## Task 15: Dark Filter Bars and Bulk Actions

Update filter bars, bulk action containers, and list filter labels.

**Files:**
- Modify: `api/app/static/dashboard.css` (bulk-actions and list-filter-bar blocks, old lines 91–136)

- [ ] **Step 1: Replace filter/bulk action CSS**

Replace the entire `.bulk-actions-container`, `.bulk-actions-bar`, `.bulk-actions-left`, `.bulk-actions-right`, `.row-select`, `.list-filter-bar`, `.list-filter-bar .form-label` blocks with:

```css
/* ── Filter + Bulk Action Bars ────────────────────────────── */
.bulk-actions-container {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 14px;
}
.bulk-actions-bar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.bulk-actions-left  { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.bulk-actions-right { display: flex; align-items: center; gap: 8px; }
.row-select { transform: scale(1.15); cursor: pointer; margin-right: 8px; }

.list-filter-bar {
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 10px 14px;
}
.list-filter-bar .form-label {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
}
```

- [ ] **Step 2: Verify filter bars**

Reload → Files and Sessions tabs. Filter bars should have dark elevated surface with subtle border. Form labels should be small uppercase caps. Search inputs and dropdowns should be dark.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark filter bars and bulk action containers"
```

---

## Task 16: Result Items, Confidence Badges, Dependency Tree

Update analysis result display components.

**Files:**
- Modify: `api/app/static/dashboard.css` (result-item, confidence-badge, dependency-tree blocks)

- [ ] **Step 1: Replace result-item and confidence CSS**

Replace:
```css
/* Results styling */
.result-item { background: white; border: 1px solid var(--border-color); border-radius: 8px; padding: 15px; margin-bottom: 10px; transition: all 0.2s ease; }
.result-item:hover { border-color: var(--primary-color); box-shadow: 0 2px 8px rgba(13, 110, 253, 0.1); }
.result-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.result-url { font-family: monospace; background: var(--light-gray); padding: 4px 8px; border-radius: 4px; word-break: break-all; }

.confidence-badge { font-size: 0.7em; padding: 4px 8px; }
.confidence-high   { background-color: var(--success-color); }
.confidence-medium { background-color: var(--warning-color); }
.confidence-low    { background-color: var(--danger-color); }
```

With:
```css
/* ── Result Items ─────────────────────────────────────────── */
.result-item {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 8px;
  transition: border-color var(--dur-fast) ease, background var(--dur-fast) ease;
}
.result-item:hover { border-color: var(--border-default); background: var(--bg-elevated); }
.result-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 10px; }
.result-url {
  font-family: var(--font-mono);
  background: var(--bg-elevated);
  border: 1px solid var(--border-subtle);
  color: var(--text-mono);
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
  word-break: break-all;
}

/* ── Confidence Badges ────────────────────────────────────── */
.confidence-badge { font-size: 0.6875rem; padding: 3px 8px; border-radius: 5px; font-weight: 500; }
.confidence-high   { background: rgba(48,209,88,0.12);  color: var(--severity-low);      border: 1px solid rgba(48,209,88,0.2); }
.confidence-medium { background: rgba(255,159,10,0.12); color: var(--severity-high);     border: 1px solid rgba(255,159,10,0.2); }
.confidence-low    { background: rgba(255,59,48,0.12);  color: var(--severity-critical); border: 1px solid rgba(255,59,48,0.2); }

/* ── Dependency Tree ──────────────────────────────────────── */
.dependency-tree { font-family: var(--font-mono); padding: 10px; font-size: 0.8125rem; color: var(--text-primary); }
.dependency-item { padding: 5px 0; border-left: 2px solid var(--border-subtle); padding-left: 15px; margin-left: 10px; color: var(--text-secondary); transition: color var(--dur-fast) ease; }
.dependency-item:hover { color: var(--text-primary); }
```

- [ ] **Step 2: Verify result components**

Navigate to a file with analysis results. Result items should have dark surface, monospace blue URL chips. Confidence badges should be dark chips (green/orange/red). Dependency tree should have dark monospace text.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark result items, confidence chips, and dependency tree"
```

---

## Task 17: Failure Panel, Analysis Context Card, Modal Form Labels

Update the remaining light-colored info panels and modal inputs.

**Files:**
- Modify: `api/app/static/dashboard.css` (failure-panel, analysis-context-card blocks, old lines 523–641)

- [ ] **Step 1: Replace failure panel CSS**

Replace:
```css
.failure-panel { background: #fff5f5; border: 1px solid #f3c5c5; border-radius: 8px; padding: 8px 10px; }
.failure-panel-title    { color: #9f1239; font-size: 0.85rem; font-weight: 700; }
.failure-panel-details  { color: #4b5563; font-size: 0.82rem; margin-top: 4px; overflow-wrap: anywhere; }
.failure-panel-guidance { color: #7f1d1d; font-size: 0.8rem; margin-top: 4px; }
```

With:
```css
/* ── Failure Panel ────────────────────────────────────────── */
.failure-panel {
  background: rgba(255,59,48,0.06);
  border: 1px solid rgba(255,59,48,0.20);
  border-left: 3px solid var(--severity-critical);
  border-radius: 8px;
  padding: 10px 12px;
}
.failure-panel-title    { color: var(--severity-critical); font-size: 0.85rem; font-weight: 700; }
.failure-panel-details  { color: var(--text-secondary); font-size: 0.82rem; margin-top: 4px; overflow-wrap: anywhere; }
.failure-panel-guidance { color: rgba(255,100,90,0.9); font-size: 0.8rem; margin-top: 4px; }
```

- [ ] **Step 2: Replace analysis context card CSS**

Replace:
```css
.analysis-context-card { background: #f8f9fa; border: 1px solid #d8dee5; border-radius: 10px; overflow: hidden; }
.analysis-context-card-header { background: #eef2f7; border-bottom: 1px solid #d8dee5; color: #1f2937; font-weight: 600; padding: 8px 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.analysis-context-grid { display: grid; gap: 6px 12px; grid-template-columns: 140px 1fr; padding: 12px; }
.analysis-context-label { color: #4b5563; font-size: 0.85rem; font-weight: 600; }
.analysis-context-value { color: #111827; font-family: "SFMono-Regular", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 0.85rem; overflow-wrap: anywhere; }
.analysis-context-value a { color: #0d6efd; text-decoration: none; }
.analysis-context-value a:hover { text-decoration: underline; }
```

With:
```css
/* ── Analysis Context Card ────────────────────────────────── */
.analysis-context-card { background: var(--bg-elevated); border: 1px solid var(--border-subtle); border-radius: 10px; overflow: hidden; }
.analysis-context-card-header { background: var(--bg-overlay); border-bottom: 1px solid var(--border-subtle); color: var(--text-secondary); font-size: 0.8125rem; font-weight: 600; padding: 8px 14px; display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.analysis-context-grid { display: grid; gap: 6px 12px; grid-template-columns: 140px 1fr; padding: 12px 14px; }
.analysis-context-label { color: var(--text-secondary); font-size: 0.8125rem; font-weight: 600; }
.analysis-context-value { color: var(--text-primary); font-family: var(--font-mono); font-size: 0.8125rem; overflow-wrap: anywhere; }
.analysis-context-value a { color: var(--accent); text-decoration: none; }
.analysis-context-value a:hover { text-decoration: underline; }
```

- [ ] **Step 3: Fix modal form label colors**

Replace hardcoded hex colors:
```css
/* ── Modal Form Labels ────────────────────────────────────── */
#sessionAnalyzeConfigModal .form-label,
#fileAnalyzeConfigModal .form-label             { font-size: 0.85rem; font-weight: 600; color: var(--text-secondary); }
#sessionAnalyzeConfigModal .form-check-label,
#fileAnalyzeConfigModal .form-check-label       { font-size: 0.9rem; color: var(--text-primary); }
#sessionAnalyzeConfigModal .form-check-input:disabled + .form-check-label,
#fileAnalyzeConfigModal .form-check-input:disabled + .form-check-label { color: var(--text-tertiary); }
.session-analyze-help { font-size: 0.85rem; margin-bottom: 14px; color: var(--text-secondary); }
.summary-wrap { max-width: 520px; overflow-wrap: anywhere; word-break: break-word; color: var(--text-secondary); }
```

- [ ] **Step 4: Verify**

Open a file with a failed analysis → failure panel should be dark red-tinted. Open analysis context card → dark elevated surface. Open "Analyze Session" modal → form labels in muted color, checkboxes with indigo checked state.

- [ ] **Step 5: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark failure panel, analysis context card, modal form labels"
```

---

## Task 18: Export Dropdown and Session Name Edit

Update the export dropdown and inline session name editor.

**Files:**
- Modify: `api/app/static/dashboard.css` (export-menu block, old lines 349–398, and session-name-display block)

- [ ] **Step 1: Replace export dropdown CSS**

Replace:
```css
.export-dropdown { ... background: white; ... }
.export-option:hover { background-color: var(--light-gray); }
```

Replace the entire `.export-menu`, `.export-dropdown`, `.export-option`, `.session-name-display`, `.session-name-edit-trigger` blocks with:

```css
/* ── Export Dropdown ──────────────────────────────────────── */
.export-menu { position: relative; display: inline-block; }
.export-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  min-width: 160px;
  z-index: 1000;
  display: none;
  overflow: hidden;
}
.export-dropdown.show { display: block; animation: tab-enter 200ms var(--ease-spring) both; }
.export-option { padding: 10px 14px; cursor: pointer; border-bottom: 1px solid var(--border-subtle); color: var(--text-primary); font-size: 0.875rem; transition: background var(--dur-fast) ease; }
.export-option:hover { background: var(--bg-overlay); }
.export-option:last-child { border-bottom: none; }

/* ── Session Name Edit ────────────────────────────────────── */
.session-name-display {
  display: inline-block;
  min-width: 140px;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: text;
  line-height: 1.2;
  padding: 2px 6px;
  margin-left: -6px;
  vertical-align: middle;
  color: var(--text-primary);
  transition: border-color var(--dur-fast) ease, background var(--dur-fast) ease;
}
.session-name-display:hover { border-color: var(--border-default); background: var(--bg-elevated); }
.session-name-display.is-editing { border-color: var(--accent); background: var(--bg-elevated); box-shadow: 0 0 0 3px var(--accent-glow); outline: none; }
.session-name-display.is-saving  { opacity: 0.7; }
.session-name-edit-trigger {
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  color: var(--text-tertiary);
  cursor: pointer;
  font-size: 0.8rem;
  line-height: 1;
  margin-left: 4px;
  padding: 3px 5px;
  vertical-align: middle;
  transition: color var(--dur-fast) ease, background var(--dur-fast) ease;
}
.session-name-edit-trigger:hover { background: var(--bg-elevated); border-color: var(--border-default); color: var(--text-primary); }
```

- [ ] **Step 2: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark export dropdown and session name inline edit control"
```

---

## Task 19: Spring Animations, Staggered Lists, Reduced Motion

Replace weak fadeIn with spring-physics tab transitions and staggered list entrance.

**Files:**
- Modify: `api/app/static/dashboard.css` (main-tab-content + loading blocks)
- Modify: `api/app/static/dashboard.js` (add `list-stagger-item` class to rendered rows)

- [ ] **Step 1: Replace animation CSS**

Replace:
```css
/* Main tab content animations */
.main-tab-content { animation: fadeIn 0.3s ease-in-out; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

/* Loading states */
.loading { opacity: 0.6; pointer-events: none; }
.spinner-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; }
```

With:
```css
/* ── Animations ───────────────────────────────────────────── */
.main-tab-content { animation: tab-enter var(--dur-enter) var(--ease-spring) both; }

@keyframes tab-enter {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}

.list-stagger-item { animation: tab-enter var(--dur-enter) var(--ease-spring) both; }
.list-stagger-item:nth-child(1)    { animation-delay:   0ms; }
.list-stagger-item:nth-child(2)    { animation-delay:  30ms; }
.list-stagger-item:nth-child(3)    { animation-delay:  60ms; }
.list-stagger-item:nth-child(4)    { animation-delay:  90ms; }
.list-stagger-item:nth-child(5)    { animation-delay: 120ms; }
.list-stagger-item:nth-child(6)    { animation-delay: 150ms; }
.list-stagger-item:nth-child(7)    { animation-delay: 180ms; }
.list-stagger-item:nth-child(8)    { animation-delay: 210ms; }
.list-stagger-item:nth-child(9)    { animation-delay: 240ms; }
.list-stagger-item:nth-child(n+10) { animation-delay: 270ms; }

/* Loading */
.loading { opacity: 0.5; pointer-events: none; }
.spinner-border { color: var(--accent) !important; }
.spinner-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10; }

/* Honour user's motion preference */
@media (prefers-reduced-motion: reduce) {
  *, .main-tab-content, .list-stagger-item, .analysis-progress {
    animation: none !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 2: Add stagger class to dynamically rendered rows in dashboard.js**

```bash
grep -n "result-item\|<tr\b\|session-row\|file-row" api/app/static/dashboard.js | head -30
```

Find where file table rows and session table rows are built (look for template literal strings that start with `` `<tr `` or `` `<div class="result-item`` inside render/list methods). Add `list-stagger-item` to each:

```js
// Result items:
`<div class="result-item list-stagger-item" ...>`

// Table rows:
`<tr class="list-stagger-item" ...>`
```

- [ ] **Step 3: Verify animations**

Reload and switch tabs — content springs in from slightly below with `cubic-bezier(0.16,1,0.3,1)`. Load files list — table rows cascade in with 30ms stagger. In OS accessibility settings, enable "Reduce Motion" — animations should become instant.

- [ ] **Step 4: Commit**

```bash
git add api/app/static/dashboard.css api/app/static/dashboard.js
git commit -m "feat(ui): spring tab animation, staggered list entrance, prefers-reduced-motion"
```

---

## Task 20: Empty States

Update empty state components to dark, contextual, generous-whitespace style.

**Files:**
- Modify: `api/app/static/dashboard.css` (empty-state block, old lines 387–398)

- [ ] **Step 1: Replace empty state CSS**

Replace:
```css
/* Empty state styling */
.empty-state { text-align: center; padding: 40px; color: #6c757d; }
.empty-state i { font-size: 3rem; margin-bottom: 15px; opacity: 0.5; }
```

With:
```css
/* ── Empty States ─────────────────────────────────────────── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 64px 24px;
  gap: 10px;
}
.empty-state i { font-size: 2.5rem; margin-bottom: 6px; opacity: 0.25; color: var(--text-secondary); }
.empty-state-title { font-size: 0.9375rem; font-weight: 600; color: var(--text-secondary); margin: 0; }
.empty-state-body  { font-size: 0.8125rem; color: var(--text-tertiary); max-width: 260px; line-height: 1.5; margin: 0; }
```

- [ ] **Step 2: Verify empty states**

Clear all files or navigate before any data loads. Empty state should: generous padding, icon at 25% opacity, readable title, muted body text.

- [ ] **Step 3: Commit**

```bash
git add api/app/static/dashboard.css
git commit -m "feat(ui): dark generous empty states"
```

---

## Task 21: Bottom Status Bar

Add a permanent 28px status bar at the very bottom showing live counts.

**Files:**
- Modify: `api/app/templates/dashboard.html` (add `<footer>` before `</div><!-- /.app-shell -->`)
- Modify: `api/app/static/dashboard.css`
- Modify: `api/app/static/dashboard.js` (`loadStatistics` → also update status bar spans)

- [ ] **Step 1: Add status bar CSS**

```css
/* ── Bottom Status Bar ────────────────────────────────────── */
.app-statusbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: 28px;
  background: rgba(13,14,17,0.95);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-top: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  padding: 0 16px;
  gap: 20px;
  z-index: 90;
  font-family: var(--font-sans);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-weight: 500;
}
.sb-item { display: flex; align-items: center; gap: 5px; }
.sb-item span { color: var(--text-secondary); }
```

- [ ] **Step 2: Add status bar HTML**

Add this just before the `</div><!-- /.app-shell -->` closing tag in `dashboard.html`:

```html
    <!-- Bottom Status Bar -->
    <footer class="app-statusbar">
        <div class="sb-item">Files: <span id="sb-files">—</span></div>
        <div class="sb-item">Sessions: <span id="sb-sessions">—</span></div>
        <div class="sb-item">Endpoints: <span id="sb-endpoints">—</span></div>
        <div class="sb-item">Secrets: <span id="sb-secrets">—</span></div>
    </footer>
```

- [ ] **Step 3: Update `loadStatistics` in dashboard.js to populate status bar**

Find `loadStatistics()` in `dashboard.js`. After the lines that update `#total-files`, `#total-sessions`, `#total-endpoints`, add:

```js
// Status bar (bottom bar)
const sbMap = { 'sb-files': data.total_files, 'sb-sessions': data.total_sessions, 'sb-endpoints': data.total_endpoints, 'sb-secrets': data.total_secrets };
Object.entries(sbMap).forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val != null ? val : '—';
});
```

- [ ] **Step 4: Verify status bar**

Reload. A 28px frosted bar at the very bottom shows Files / Sessions / Endpoints / Secrets counts. As statistics load, the counts populate. Toast appears above it (52px from bottom).

- [ ] **Step 5: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css api/app/static/dashboard.js
git commit -m "feat(ui): persistent bottom status bar with live stats"
```

---

## Task 22: Responsive Layout + Page Title + Final Polish

Responsive sidebar collapse, fix page title, clean up any remaining light-mode artifacts.

**Files:**
- Modify: `api/app/templates/dashboard.html:4-6` (title + meta)
- Modify: `api/app/static/dashboard.css` (responsive breakpoints)

- [ ] **Step 1: Fix page title and add color-scheme meta**

Replace:
```html
    <title>🔍 JavaScript Security Extractor</title>
```
With:
```html
    <title>JS Security Extractor</title>
    <meta name="color-scheme" content="dark">
```

- [ ] **Step 2: Replace responsive block**

Replace:
```css
@media (max-width: 768px) {
    .container-fluid { padding: 10px; }
    .col-md-3, .col-md-9 { margin-bottom: 20px; }
    .stat-number { font-size: 1.5rem; }
    .card-body { padding: 15px; }
}
```

With:
```css
/* ── Responsive ───────────────────────────────────────────── */
@media (max-width: 900px) {
  .app-sidebar { width: 200px; }
}
@media (max-width: 768px) {
  .app-sidebar { width: 56px; padding: 16px 4px; }
  .nav-item .nav-label { display: none; }
  .nav-section-label  { display: none; }
  .stat-bento         { display: none; }
  .app-main           { padding: 16px 12px; padding-bottom: 52px; }
  .analysis-context-grid { grid-template-columns: 1fr; }
  .card-body { padding: 16px; }
  .stat-num  { font-size: 1.25rem; }
}
@media (max-width: 480px) {
  .app-sidebar { display: none; }
  .app-main    { padding: 10px 8px; padding-bottom: 52px; }
}
```

- [ ] **Step 3: Add `.nav-label` span to nav items in dashboard.html**

Wrap the text label in each nav-item button with `<span class="nav-label">`:

```html
<button class="nav-item active" id="nav-analysis" onclick="showAnalysisTab()">
    <svg ...>...</svg>
    <span class="nav-label">Scan</span>
</button>
```

Do this for all three nav buttons (Scan, Files, Sessions).

- [ ] **Step 4: Full cross-tab visual review**

Walk through every part of the UI:
1. Scan tab: URL input, code viewer textarea, analysis type radios, options checkboxes, "Start Analysis" button
2. Scan tab with results: endpoints/secrets/deps/sourcemaps result tabs
3. Files tab: filter bar, file table, bulk actions, status badges on rows
4. Sessions tab: session list, "Create New Session" button
5. Open session files view: per-file analysis status, sourcemap validation summary
6. Open "Analyze Session" modal: dark form fields, quick/advanced buttons
7. Open reconstructed sources modal: dark content
8. Trigger an analysis: progress toast slides up, progress bar fills, slides down on complete
9. At 768px viewport width: sidebar collapses to icon-only
10. At 480px: sidebar hidden

- [ ] **Step 5: Commit**

```bash
git add api/app/templates/dashboard.html api/app/static/dashboard.css
git commit -m "feat(ui): responsive sidebar collapse, fix page title, final polish pass"
```

---

## Self-Review

### Spec Coverage

| UI Review Requirement | Task |
|---|---|
| Dark design token system | 1 |
| Inter + JetBrains Mono fonts | 1 |
| Dark base body + Bootstrap overrides | 2 |
| Slim frosted titlebar with pulse dot | 3 |
| App shell layout (flex, sidebar + main) | 4 |
| Sidebar nav rail with Lucide SVG icons | 5 |
| Active state sync on tab switch | 5 |
| Bento stat grid (5 tiles) | 6 |
| Dark glass panels (no gradient headers) | 7 |
| Dark buttons (all variants) | 8 |
| Dark data tables (uppercase headers, mono URLs) | 9 |
| Pulse status dots + dark badge chips | 10 |
| Secret blur masking + threat card | 11 |
| Terminal code viewer | 12 |
| Sourcemap pipeline indicator CSS | 13 |
| Slide-up spring progress toast | 14 |
| Dark filter/bulk action bars | 15 |
| Dark result items + confidence badges + dep tree | 16 |
| Failure panel + analysis context card dark | 17 |
| Export dropdown + session name edit dark | 18 |
| Spring tab animation + staggered list entrance | 19 |
| prefers-reduced-motion support | 19 |
| Dark empty states | 20 |
| Bottom status bar with live counts | 21 |
| Responsive sidebar collapse | 22 |
| Page title (emoji removed) + color-scheme meta | 22 |

**Not covered (Phase 2 — separate plan):** Chrome extension popup dark theme (independent subsystem, zero shared HTML/CSS).

### Placeholder Scan

No TBD, TODO, or vague steps found. All CSS blocks are complete with exact property values. All JS changes specify exact method names to search for.

### ID / Token Consistency

- `#api-status-dot`, `#api-status-text` — defined in Task 3 HTML, referenced in Task 3 JS
- `#total-secrets`, `#total-sourcemaps` — defined in Task 6 HTML
- `#sb-files`, `#sb-sessions`, `#sb-endpoints`, `#sb-secrets` — defined in Task 21 HTML, referenced in Task 21 JS
- `#toast-progress-fill` — defined in Task 14 HTML, referenced in Task 14 JS
- `.list-stagger-item` — defined in Task 19 CSS, applied in Task 19 JS
- `.nav-label` — defined in Task 22 CSS, added to HTML in Task 22
- All legacy element IDs (`#total-files`, `#total-sessions`, `#total-endpoints`, `#analysis-progress`) preserved unchanged

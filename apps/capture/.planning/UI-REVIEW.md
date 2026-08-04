# JS Security Extractor — UI Review

**Audited:** 2026-04-19
**Baseline:** Abstract 6-pillar standards (no UI-SPEC.md present)
**Screenshots:** Not captured (no dev server detected at localhost:3000, 5173, or 8080)

---

## Pillar Scores

| Pillar | Score | Key Finding |
|--------|-------|-------------|
| 1. Copywriting | 3/4 | Mostly purposeful; a hardcoded test domain prefills the Create Session modal and `getEmptyState` omits descriptive body text |
| 2. Visuals | 3/4 | Good hierarchy and icon coverage; `getEmptyState` renders only an icon + single-line `<p>`, losing the `.empty-state-title`/`.empty-state-body` split defined in CSS |
| 3. Color | 2/4 | Dashboard (warm #356AE6) and Chrome extension (iOS-blue #0a84ff) use entirely different accent palettes — brand incoherence across two surfaces |
| 4. Typography | 2/4 | 14 distinct rem font-size values in dashboard.css far exceed a healthy 4–5 stop scale; several one-off sizes (0.775rem, 0.82rem, 0.85rem, 0.9rem, 1.05rem) are not reused elsewhere |
| 5. Spacing | 3/4 | CSS uses raw pixel values throughout rather than a named token scale; spacing is internally consistent but arbitrary values (5px, 7px, 14px, 15px) slightly break rhythm |
| 6. Experience Design | 3/4 | Loading, error, disabled, and destructive-confirm states are all handled; alerts use magic-number inline styles and `window.confirm()` for destructive actions is jarring |

**Overall: 16/24**

---

## Top 3 Priority Fixes

1. **Extension/dashboard accent color mismatch** — A user switching between the Chrome popup and the web dashboard sees two completely different brand blues (#0a84ff vs #356AE6), eroding trust in a security tool. Create a shared `--accent` token file or document the intended palette split and apply it consistently to both surfaces.

2. **14-stop font-size scale in dashboard.css** — The proliferation of near-identical sizes (0.8rem, 0.8125rem, 0.82rem, 0.85rem) makes type maintenance brittle and produces subtle unintentional size variation. Collapse to a 5-stop scale: `--text-xs: 0.6875rem`, `--text-sm: 0.75rem`, `--text-base: 0.875rem`, `--text-md: 0.9375rem`, `--text-lg: 1.5rem` and apply via CSS custom properties everywhere `font-size` appears.

3. **`getEmptyState` produces degraded empty states** — The helper at `dashboard.js:3592` emits only `<i class="fas...">` + `<p>message</p>`, but `dashboard.css:538–539` defines `.empty-state-title` (semibold heading) and `.empty-state-body` (muted subtext, max-width 260px). Every empty result panel — endpoints, secrets, dependencies, source maps, files, sessions — falls back to an unstyled `<p>` inside `.empty-state`. Fix: update `getEmptyState(title, body, icon)` to emit the `.empty-state-title` / `.empty-state-body` structure, and add a contextual body string per call site (e.g., "Run an analysis to populate results" for endpoints).

---

## Detailed Findings

### Pillar 1: Copywriting (3/4)

**Strengths**
- CTAs are action-oriented throughout: "Start Analysis" (dashboard.html:193), "Start Session Crawl" (dashboard.html:626), "Start Capture" / "Stop Capture" (popup.html:267–268), "Quick Run" / "Start Advanced Run" (dashboard.html:540–547).
- Error alerts from `showAlert()` carry specific context: "Session analysis failed: {message}", "Failed to start session crawl: {message}", "Please provide a JavaScript URL" — all better than generic "Something went wrong."
- Destructive confirmations use natural language: "Delete this file and its stored analysis results? This cannot be undone." (dashboard.js:2726).
- Failure panel guidance copy in `dashboard-failure-utils.js` is contextually specific per failure source (sourcemap vs capture_fetch vs analysis).

**Issues**
- `dashboard.js:793` — `openCreateSessionModal()` hard-prefills the target URL with `'https://wishandwash.co.il'`, a developer test domain. Any new user opens the modal and sees someone else's domain. Fix: remove the prefill or use an empty placeholder.
- `dashboard.js:3592–3598` — `getEmptyState()` emits `<p>message</p>` directly inside `.empty-state`, bypassing the `.empty-state-title` / `.empty-state-body` classes. All empty states read as flat unstyled text. The result tab empty states ("No endpoints found", "No secrets found") lack a subtext guiding next action.
- `chrome-extension/options.html:283–284` — Save / Reset buttons use emoji (`💾`, `🔄`). Emoji in button labels renders inconsistently across OS and screen readers, and is inconsistent with the icon-free style of the main dashboard. Use Font Awesome icons or text-only labels.
- `chrome-extension/popup.html:247` — Diagnostic text reads "Processed: 0 | Failed: 0" as initial state — a developer-facing format exposed directly to users. Consider humanizing to "No files processed yet."

### Pillar 2: Visuals (3/4)

**Strengths**
- Clear three-level hierarchy: titlebar > sidebar nav + stat bento > main content area. Status bar provides persistent context.
- Sidebar navigation items all have `aria-label` attributes (dashboard.html:35, 40, 45) and inline SVGs carry `aria-hidden="true"`.
- Secret values implement a blur-mask pattern with a toggle button (dashboard.css:346) — appropriate for sensitive data.
- Color-coded confidence badges (high/medium/low), severity-left-bordered secret items, and the pipeline step component (`dashboard.css:695–708`) provide meaningful visual differentiation.
- Stat bento tiles use semantic accent colors (green for endpoints, red for secrets, indigo for sourcemaps) with accessible contrast.
- Spring-eased tab transitions (`dashboard.css:394–399`) and list stagger animations give the app a polished feel.
- `prefers-reduced-motion` media query is respected at `dashboard.css:419–424`.

**Issues**
- `getEmptyState()` (dashboard.js:3592–3598) produces only `<i class="fas fa-${icon}"></i><p>${message}</p>`. The defined `.empty-state` container styles expect `.empty-state-title` + `.empty-state-body` children. The icon renders at `opacity: 0.4` (dashboard.css:537) but the text lands in an unstyled `<p>` rather than the styled `.empty-state-title`. The visual result is significantly weaker than the CSS was designed to deliver.
- `chrome-extension/popup.html` — The settings gear icon at line 237 has no `aria-label` or `title` attribute. It is an SVG with no text equivalent. Fix: add `aria-label="Open Settings"` to the `<svg id="settingsBtn">` element.
- The dashboard's "Analysis Results" section header (`dashboard.html:205`) uses a `<small>` context line (`id="results-context"`) that defaults to "No analysis context selected." — this is visible before any analysis is run and reads as an error state when it is not.
- Buttons in the Files card header (Load All Files, All Sessions, Map Diagnostics, Refresh — dashboard.html:275–286) carry FontAwesome icons but use the same `fa-layer-group` icon for two different actions ("Load All Files" and "All Sessions"), creating icon ambiguity.

### Pillar 3: Color (2/4)

**Dashboard color system (good)**
- Well-structured token set in `dashboard.css:5–77`: warm paper backgrounds, three text hierarchy levels, single accent, semantic severity palette. Bootstrap tokens are overridden to keep class names consistent with the custom palette.
- Badges use tinted backgrounds rather than solid fills — appropriate low-weight color application.
- No hardcoded hex values leak into dashboard.js (confirmed by grep).
- 60/30/10 split is roughly honored: warm neutrals dominate, accent (#356AE6) appears on interactive controls and active states.

**Issues**
- **Critical brand split**: `dashboard.css --accent: #356AE6` (warm cornflower blue) vs `popup.html/options.html --accent: #0a84ff` (iOS system blue). These are different hues, not just different shades. A user looking at the extension popup versus the web dashboard sees two distinct product identities. This is the highest-priority color fix.
- `popup.html:79` — `.status-indicator` default background is hardcoded `#cfd3d9` inline rather than using `var(--muted)` or a named token. Minor, but the token system exists and is not used here.
- `popup.html:14` — `--accent-2: #111315` (near-black) is applied as the primary button background (`btn-primary` = Start Capture). A near-black button labeled "Start Capture" reads as a neutral/secondary action, not primary. On the dashboard, green (`btn-success`) is used for the primary start action — the semantic color convention is inverted in the extension.
- `options.html:138–144` — Status feedback colors (`#1d5b35`, `#7a1f1f`) are hardcoded inline and not derived from the token system.

### Pillar 4: Typography (2/4)

**Strengths**
- Two font families are correctly tokenized: `--font-sans: 'Inter'` and `--font-mono: 'JetBrains Mono'`, with appropriate fallback stacks.
- Semantic application is consistent: mono for URLs, code, secret values, context blocks; sans for all UI chrome.
- Inter is loaded via Google Fonts with optical size range (`opsz,wght@0,14..32`) — correct for variable font usage.
- `font-variant-numeric: tabular-nums` is applied to stat numbers (`dashboard.css:880`) — good numeric alignment.

**Issues**
- **14 distinct rem font-size values in dashboard.css** (0.6875, 0.75, 0.775, 0.8, 0.8125, 0.82, 0.85, 0.875, 0.9, 0.9375, 1.05, 1.25, 1.5, 2.5rem). The healthy maximum for an application UI is 5–6 stops. The presence of nearly identical values like 0.8rem, 0.8125rem, 0.82rem, and 0.85rem — all in the "small UI text" range — suggests incremental patches rather than a deliberate scale.
- One-off sizes with single usage: `0.775rem` (sourcemap-lifecycle-line, dashboard.css:721), `0.82rem` (failure-panel-details, dashboard.css:664), `0.85rem` (failure-panel-title, session-analyze-help, modal form labels), `0.9rem` (modal check labels), `1.05rem` (modal title override, dashboard.css:648–652). These are not reused and do not correspond to a named step.
- Chrome extension (`popup.html`, `options.html`) defines no shared font token — sizes are hardcoded as `px` values (11px, 12px, 13px, 14px, 18px, 26px). No overlap with the dashboard rem scale. Extension headings (`h2` at 18px in popup, `h1` implied in options) have no declared line-height.
- `card-header h5/h6` are forced to `0.8125rem` uppercase via CSS override (dashboard.css:186–190), meaning `<h5>` elements visually render as tiny all-caps labels. This breaks heading semantics — screenreaders announce these as `<h5>` headings but they look like overlines.

### Pillar 5: Spacing (3/4)

**Strengths**
- Spacing is applied exclusively through CSS classes and a custom property system — no arbitrary Tailwind-style bracket values.
- Inline `style=` attributes in dashboard.html are limited to `display: none` toggling — not used for layout or spacing values.
- Modal padding is consistent: 20px/24px across header/body (dashboard.css:127–129).
- Card body padding is uniform at 20px (dashboard.css:193).
- Responsive breakpoints collapse sidebar gracefully at 900px → 768px → 480px (dashboard.css:427–443).

**Issues**
- No explicit spacing scale is defined. Values used include: 3, 4, 5, 6, 7, 8, 10, 12, 14, 15, 16, 18, 20, 24, 36, 38px. While Bootstrap's 4px grid underlies the `g-2`/`g-3` Bootstrap classes in HTML, the custom CSS components use arbitrary values (5px gaps, 7px dots, 14px/15px padding variants) that are not multiples of 4.
- `dependency-item` (dashboard.css:373) uses `padding-left: 15px` and `margin-left: 10px` — neither is on the 4px or 8px grid. `15px padding-left` should be `16px`.
- `.stat-lbl` (dashboard.css:888) has `margin-top: 4px` which is fine, but `.stat-tile` inner padding is `10px 10px 8px` (dashboard.css:869) — asymmetric top/bottom with no apparent reason.
- `showAlert` in dashboard.js:3561 hardcodes spacing inline: `'top: 20px; right: 20px; z-index: 9999; min-width: 300px;'`. These values live outside the CSS token system and will not respond to theme changes.
- Status bar height is 28px (dashboard.css:547), which is not on the 4px grid (should be 28px — this is actually fine at 7×4). However, the progress toast bottom offset is `52px` (dashboard.css:568), which suggests it clears a `28px` bar + `24px` gap — reasonable but magic-number.

### Pillar 6: Experience Design (3/4)

**Strengths**
- Comprehensive loading state coverage: loading modal for analysis (dashboard.html:381–393), per-modal spinners for reconstructed sources and session summary, spinner-in-button for "Starting..." state (dashboard.js:776), progress toast bar for session analysis (dashboard.html:724–728), `Analyzing...` / `Stopping...` button label morphing.
- Error states everywhere: `alert-danger` panels in modals, `showAlert()` on every API failure, `dashboard.js:122` catch blocks (122 error-related patterns), failure panel with source-inferred guidance via `DashboardFailureUtils`.
- All destructive actions (file delete, bulk delete, session delete, clear all files, reset settings) use `window.confirm()` with explicit consequence language ("This cannot be undone.").
- Disabled-state management on modal buttons during in-flight operations via `setSessionAnalyzeModalBusy`, `setFileAnalyzeModalBusy`, `setCreateSessionModalBusy`.
- Client-side filtering with 180ms debounce on both Files and Sessions tabs.
- Session analysis persist defaults via `localStorage` (dashboard.js:349–370) — reduces friction on repeated runs.
- URL-based routing with `history.pushState`/`popstate` support — browser back/forward works across tabs.

**Issues**
- `showAlert()` (dashboard.js:3558–3575) mounts alerts with `z-index: 9999` as `position: fixed` elements appended to `<body>`. This is not integrated with Bootstrap's modal stacking context (`z-index: 1055` for modals). If an alert fires while a Bootstrap modal is open, the alert renders behind the modal backdrop. Fix: move `showAlert` styling to a CSS class rather than inline `style.cssText`, and use `z-index: 1060` (above Bootstrap modals).
- `getEmptyState()` (dashboard.js:3592) returns only `<i>` + `<p>` — the empty panels for Endpoints, Secrets, Dependencies, and Source Maps lack actionable body text. The CSS framework supports a two-line pattern but it is not used. A user seeing "No endpoints found" gets no guidance (e.g., "Run an analysis or check extractor settings").
- `window.confirm()` for destructive operations (dashboard.js:2459, 2726, 3093, 3511; popup.js:162; options.js:87) is a browser-native blocking dialog that: cannot be styled, is blocked on some browsers in extension contexts, and provides no loading feedback after confirmation. For a security tool that users run frequently, a styled in-modal confirmation with a spinner would be more polished.
- The `analysis-tab` shows "No analysis context selected." as default placeholder text in `#results-context` (dashboard.html:208). This reads as an error state to new users before any action is taken. Replace with an empty string or a soft instructional hint.
- Chrome extension popup polling interval is 2000ms (popup.js:186–189), meaning UI state can lag up to 2 seconds behind reality. For a capture tool this is acceptable, but start/stop button state relies on this poll — a user who clicks Start then immediately looks at the button may see it unchanged for up to 2 seconds.

---

## Files Audited

- `api/app/templates/dashboard.html` — Main dashboard SPA shell (735 lines)
- `api/app/static/dashboard.css` — Custom design system and Bootstrap overrides (897 lines)
- `api/app/static/dashboard.js` — Dashboard class: routing, API calls, rendering (~3900+ lines, reviewed via targeted reads)
- `api/app/static/dashboard-failure-utils.js` — Failure classification and guidance copy (78 lines)
- `chrome-extension/popup.html` — Extension popup UI (279 lines)
- `chrome-extension/popup.js` — Popup event handling and polling (190 lines)
- `chrome-extension/options.html` — Extension settings page (290 lines)
- `chrome-extension/options.js` — Settings persistence (121 lines)

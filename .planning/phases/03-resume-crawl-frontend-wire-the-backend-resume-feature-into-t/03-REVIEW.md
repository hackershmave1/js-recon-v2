---
phase: 03-resume-crawl-frontend-wire-the-backend-resume-feature-into-t
reviewed: 2026-04-20T14:04:29Z
depth: standard
status: issues
files_reviewed: 1
files_reviewed_list:
  - api/app/static/dashboard.js
findings:
  critical:
    - id: CR-01
      file: api/app/static/dashboard.js:3623
      issue: "DOM XSS: `showAlert()` renders `${message}` via `innerHTML`, and many call sites pass backend-provided strings (e.g., `error.response.data.detail`). A malicious/compromised backend (or attacker-controlled upstream content reflected by the backend) can inject HTML/JS into the dashboard."
      fix: "Build the alert DOM with `textContent` (or escape the message) instead of interpolating into `innerHTML`. Example: create a `span` with `textContent = String(message ?? '')` and append a close button element."
    - id: CR-02
      file: api/app/static/dashboard.js:1869
      issue: "DOM XSS / JS injection: `displaySecrets()` embeds the secret value into an inline `onclick` handler using single quotes. `escapeHtml()` is HTML-escaping, not JavaScript-string escaping; a secret containing `'` or `\\` can break out of the string and run arbitrary JS when the user clicks the toggle."
      fix: "Remove inline handlers for dynamic values. Store secrets in JS state (e.g., `this.currentSecrets = secrets`) and call `toggleSecret(index)`; look up the value inside `toggleSecret`. Alternatively, bind listeners after render and keep the value in a closure, or store an encoded value in `data-*` and decode safely."
    - id: CR-03
      file: api/app/static/dashboard.js:2266
      issue: "DOM XSS: error UIs interpolate `${error.message}` directly into `innerHTML` (e.g., file list and sessions list). `error.message` may contain attacker-controlled content (including from backend error payloads), resulting in HTML injection."
      fix: "Escape the error string (`this.escapeHtml(...)`) before inserting, or render an element and set `textContent`. Apply similarly at `api/app/static/dashboard.js:2931`."
    - id: CR-04
      file: api/app/static/dashboard.js:1930
      issue: "DOM XSS: `displaySourceMap()` interpolates `sourcemap.error` into HTML without escaping when `sourcemap.success === false`."
      fix: "Wrap `sourcemap.error` with `this.escapeHtml(...)` (or set it via `textContent`) before inserting into the DOM."
    - id: CR-05
      file: api/app/static/dashboard.js:1801
      issue: "Attribute/content injection risk: endpoint/secret `confidence` is used to form CSS class names and is rendered without escaping in badges. If the backend ever returns unexpected strings, this can break markup and may enable attribute injection."
      fix: "Whitelist allowed confidence values (e.g., `low|medium|high`) and default unknown values. Always render the label with `this.escapeHtml(...)`."
  warning:
    - id: WR-01
      file: api/app/static/dashboard.js:1253
      issue: "Potential runtime crashes if DOM structure changes: several functions assume elements exist and immediately dereference them (e.g., `validateInput()` reads `.value` from `#js-content`/`#js-url`; `setupDragAndDrop()` calls `addEventListener` on `#js-content`; `showLoadingModal()` assumes `#loadingModal` exists; `loadSessions()` assumes `#sessions-content` exists). This increases UI/UX regression risk."
      fix: "Add null checks (or early returns) consistently before dereferencing DOM nodes, mirroring the optional-chaining style used elsewhere in the file."
    - id: WR-02
      file: api/app/static/dashboard.js:1971
      issue: "`toggleSecret()` assumes `value` is a string (`value.length`). If `secret.value`/`secret.match` is ever non-string, this will throw."
      fix: "Coerce `value` once (`const text = String(value ?? '')`) and use `text.length`/`textContent = text`."
  info:
    - id: IN-01
      file: api/app/static/dashboard.js:1
      issue: "Maintainability: `dashboard.js` is very large (~4405 lines) with many string-templated HTML blocks and inline event handlers. This makes correctness/security auditing and UI changes harder."
      fix: "Consider extracting render helpers per feature (analysis/files/sessions/modals), and prefer `addEventListener` + DOM APIs (or a small templating layer) to reduce inline-handler/string-HTML risks."
---

# Phase 03: Code Review Report

**Reviewed:** 2026-04-20T14:04:29Z  
**Depth:** standard  
**Files Reviewed:** 1  
**Status:** issues

## Summary

Reviewed `api/app/static/dashboard.js` for correctness, security, UI/UX regression risk, and maintainability. The primary concerns are multiple DOM XSS vectors caused by inserting untrusted strings into `innerHTML` and embedding dynamic data into inline `onclick` handlers.

## Critical Issues

### CR-01: DOM XSS in `showAlert()`

**File:** `api/app/static/dashboard.js:3623`  
**Issue:** `showAlert()` interpolates `message` into `innerHTML`; several call sites pass backend-provided strings.  
**Fix:** Build DOM with `textContent` (recommended) or escape before inserting.

### CR-02: Secret value injection via inline `onclick`

**File:** `api/app/static/dashboard.js:1869`  
**Issue:** Secret values are placed inside a JS string in an HTML attribute; HTML escaping does not make it safe for JS-string context.  
**Fix:** Avoid inline handlers for dynamic values; pass only an index and look up the value in JS state, or bind listeners programmatically.

### CR-03: Unescaped `error.message` inserted into `innerHTML`

**File:** `api/app/static/dashboard.js:2266` (also `api/app/static/dashboard.js:2931`)  
**Issue:** Error strings can inject HTML into the page.  
**Fix:** Escape or render as text nodes.

### CR-04: Unescaped `sourcemap.error` inserted into HTML

**File:** `api/app/static/dashboard.js:1930`  
**Issue:** Backend error text is inserted without escaping.  
**Fix:** Escape or render as text nodes.

### CR-05: Confidence label/class should be validated and escaped

**File:** `api/app/static/dashboard.js:1801`  
**Issue:** Untrusted strings are used in class names and rendered as HTML.  
**Fix:** Whitelist allowed values; escape output.

## Warnings

### WR-01: Missing DOM null checks can cause regressions

**File:** `api/app/static/dashboard.js:1253`  
**Issue:** Direct dereferences of potentially-missing DOM nodes.  
**Fix:** Add guards/early returns.

### WR-02: `toggleSecret()` assumes a string

**File:** `api/app/static/dashboard.js:1971`  
**Issue:** Non-string values can throw.  
**Fix:** Coerce to string.

## Info

### IN-01: Large monolithic file increases risk

**File:** `api/app/static/dashboard.js:1`  
**Issue:** Size and inline handler/template usage increase maintenance and security review burden.  
**Fix:** Modularize and reduce string-HTML usage.

---

_Reviewed: 2026-04-20T14:04:29Z_  
_Reviewer: Claude (gsd-code-reviewer)_  
_Depth: standard_

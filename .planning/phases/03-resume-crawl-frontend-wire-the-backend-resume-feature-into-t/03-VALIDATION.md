---
phase: 3
slug: 03-resume-crawl-frontend
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-20
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Manual browser verification (no JS test suite in this project) |
| **Config file** | none |
| **Quick run command** | Open dashboard, check session row renders correctly |
| **Full suite command** | Perform full resume flow: stop a crawl, click Continue Crawl, verify new assets added |
| **Estimated runtime** | ~2 minutes manual |

---

## Sampling Rate

- **After every task commit:** Reload dashboard, check session row buttons render without JS errors
- **After every plan wave:** Run full manual flow (see Per-Task map below)
- **Before `/gsd-verify-work`:** Full flow must complete without errors

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-01 | 01 | 1 | — | — | Button only shown when session has assets + stopped job | manual | grep dashboard.js for continueCrawl | ✅ | ⬜ pending |
| 3-01-02 | 01 | 1 | — | — | POST includes resume:true and correct URL | manual | grep dashboard.js for 'resume.*true' | ✅ | ⬜ pending |
| 3-01-03 | 01 | 1 | — | — | Button disabled when crawl active | manual | visual check | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements (pure frontend JS change, no test framework in project).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Continue Crawl button appears on stopped session with assets | Phase 3 goal | No automated JS test suite | 1. Create session, run crawl, stop it. 2. Reload sessions tab. 3. Verify button visible. |
| Resume POST sends correct payload with resume:true | Phase 3 goal | Network verification | Open DevTools Network tab, click Continue Crawl, verify POST body contains resume:true and correct URL |
| Button disabled while crawl running | Phase 3 goal | Visual state | Start a resume crawl, verify button becomes disabled during run |
| Button hidden when session has no prior tracked job | Phase 3 goal | Visual state | Open extension-created session with files, verify no Continue Crawl button |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 120s (manual)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending

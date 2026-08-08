---
status: accepted
date: 2026-08-08
---

# 8. One hardened harness for out-of-process engines

## Context and Problem Statement

Two analysis engines (the Kingfisher secret scanner and Sourcemapper) and the katana
crawler are external binaries the worker shells out to, over attacker-influenced input (a
target's JS, source maps, crawl output). Each such subprocess is a resource-exhaustion and
misbehaviour risk — runaway CPU/time, unbounded output, unexpected exit. How should these
be run safely?

## Considered Options

* **One shared hardened harness** — a single wrapper enforcing a wall-clock timeout, an
  output-size cap, an explicit acceptable-exit-code set, and a non-root container user.
* **Per-engine ad-hoc `subprocess` calls** — each call site sets its own (or no) limits.

## Decision Outcome

Chosen option: a **single hardened harness** (`findings/engines.py`) that the
out-of-process *findings* engines (Kingfisher's secret scan, Sourcemapper's source
recovery) run through, so the safety controls live in one audited place rather than being
re-derived (and forgotten) per call site. The container runs as a non-root user. Discovery
(katana) uses a parallel harness (`discover/harness.py`) with the same posture plus
process-group kill for headless-Chrome grandchildren.

### Consequences

* Good — timeout / output-cap / exit-code policy is defined once and inherited by every
  engine; adding an engine means reusing the harness, not re-inventing limits.
* Good — an engine that hangs or floods output is bounded deterministically instead of
  wedging the worker.
* Neutral — a genuinely new execution shape (e.g. katana's headless grandchildren) needs a
  deliberate harness extension (`discover/harness.py`), not a bypass.

### Confirmation

`findings/engines.py:1-21` (the shared harness: wall-clock timeout, output-size cap,
acceptable-exit-code set, non-root). Findings engines route through it —
`findings/kingfisher.py` and `findings/sourcemapper.py` both `import engines`. Discovery
uses the parallel `discover/harness.py:94-100` (`killpg` of the whole process group;
docstring `:1-11`). Covered by the engine contract tests (run with
`RECON_REQUIRE_ENGINES=1`).

## More Information

Recorded retroactively 2026-08-08 (DEBT D10). See `docs/ARCHITECTURE.md` ("Engines": "the
out-of-process engines run through one hardened harness ... so the safety controls live in
one place").

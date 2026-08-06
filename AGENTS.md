# AGENTS.md

This repository's standards for contributors and coding agents live in
[CLAUDE.md](./CLAUDE.md) (same directory) — layout, how to run + test, the enforced
CI gates, the branch model, and conventions. Tracked tech debt is in
[DEBT.md](./DEBT.md).

Single source of truth is `CLAUDE.md`; this pointer exists so agents that look for
`AGENTS.md` find their way there. (On a dev machine with symlink support you can
replace this file with a symlink to `CLAUDE.md` to guarantee they never drift.)

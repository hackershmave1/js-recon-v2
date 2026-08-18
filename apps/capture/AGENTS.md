# AGENTS.md

This directory's guide for contributors and coding agents is [`CLAUDE.md`](./CLAUDE.md)
(same directory) — what the capture extension is, how to build + test it, and its
load-bearing MV3 durability invariants. Repo-wide standards live in the repo-root
[`CLAUDE.md`](../../CLAUDE.md).

Single source of truth is `CLAUDE.md`; this pointer exists so agents that look for
`AGENTS.md` find their way there. (On a dev machine with symlink support you can replace
this file with a symlink to `CLAUDE.md` to guarantee they never drift.)

---
# One of: proposed | accepted | rejected | deprecated | "superseded by ADR-XXXX"
status: "proposed"
date: YYYY-MM-DD
# Optional: supersedes / superseded-by: ADR-XXXX
---

# NNNN. Short title of the decision

> Load-bearing sections are **Context and Problem Statement**, **Considered Options**,
> **Decision Outcome** (+ Consequences) and **Confirmation**. The rest are optional
> (MADR-native) — delete what you don't use. Keep an ADR to ~1 page: it distils the
> decision and *links* to the detailed slice spec, it does not copy it.

## Context and Problem Statement

{What forces are at play; what has to be decided and why. Two or three sentences, or an
illustrative story. Point at the components involved.}

## Decision Drivers <!-- optional -->

* {a desired quality, constraint, or force}

## Considered Options

* {option 1}
* {option 2}

## Decision Outcome

Chosen option: "{option}", because {justification}.

### Consequences

* Good, because {…}
* Bad, because {…}

### Confirmation

{The exact code / test / requirement that enforces this decision — the anti-drift anchor.
A reviewer (or a future contributor) checks the decision still holds by looking here.}

## More Information <!-- optional -->

{Links to the originating slice spec, related ADRs, and any off-repo rationale. For a
backfill, note "Recorded retroactively YYYY-MM-DD" so the file date is not read as the
decision date.}

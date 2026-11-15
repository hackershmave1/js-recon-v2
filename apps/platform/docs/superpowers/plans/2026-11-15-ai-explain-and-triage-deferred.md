# AI explain and AI triage for findings: deferred plan

> **Status: deferred, not started.** Moved off the issue tracker on 2026-11-15. These are new
> features, not defects, so they wait until the problem backlog (the research-workflow build, the
> walkthrough frictions and the open `DEBT.md` items) is worked down. Nothing here is decided.

## Where this came from

Two ideas on the unified research workflow map (#174): **AI explain** (#182) and **AI triage**
(#183). Both tickets were closed as deferred on 2026-11-15 and their questions are kept below so
the work can resume without re-deriving them. The map's destination no longer includes them.

## The two ideas

**AI explain.** A side chat on a finding that says what it is, how sure the tool is and what to
check next. It answers walkthrough friction F16 ("a finding's detail doesn't say what it is or what
to do"), but F16 needs a **non-AI baseline first**: the finding detail should show the kind of
finding, its confidence and why it has its severity from data the platform already holds.

**AI triage.** An AI verdict on a finding (valid, false positive, needs review, with reasoning).
A human may confirm it before it changes the finding's triage state.

## Questions carried over from the tickets

1. **What is sent to the model:** the finding, its occurrence snippet, surrounding source, the
   session scope, related findings? What is redacted (secret values never leave the platform
   unredacted)? `DEBT.md` D64 already decides the architecture for giving a model code context
   (finding occurrence to run asset to object storage, no web search, no tool loop, no RAG) but
   defers building it.
2. **How the side chat looks and streams**, prototyped on one finding.
3. **What triage decides and at what granularity:** one finding, a selection, or a whole run.
4. **Does a human confirm** before the triage state changes, and how is an AI verdict shown next
   to a human one (the audit trail must name the real actor: D48).

## What already exists to build on

* Team-level LLM provider settings and model presets (plans and specs of 2026-10-06 and
  2026-10-07) and per-call records of prompt, response, latency and cost (`llm/calls.py`).
* Threat-model generation, which already assembles findings into a prompt.
* Per-finding triage statuses and controls in the workspace.
* `DEBT.md` D49 and D50 (findings prioritization and triage at scale) cover the non-AI side of
  the same problem.

## Prerequisites before picking this up

1. The research-workflow build is far enough along that findings are scoped correctly
   (ADR-0010), so the model is not asked about out-of-scope content.
2. The non-AI finding detail baseline for F16 is in.
3. A decision on D64 (code context), since explain without code context produces the generic
   output D64 describes.

## How to resume

Grill AI triage (#183's questions) and prototype one finding's side chat (#182's), then write a
real spec and implementation plan in this folder.

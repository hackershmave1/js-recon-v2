"""Fast-lane tests for the D31 curtailment-honesty flag.

When the one-shot AST node budget (`_MAX_AST_NODES`) bounds ``extract()``'s two expensive
passes to a prefix, the tail-drop is no longer SILENT: ``Extraction.curtailed`` is set and
rides the coverage read model so a partial extract is reported truthfully (REQ-C2). These
tests pin the honesty signal and its propagation through the merge / read-model glue.

Pure / monkeypatched — no stack. The DoS-guard TIMING (that curtailment actually bounds
wall-clock) is covered by ``extract_test.py::test_node_budget_curtails_pathological_tree_dos_guard``
(owned by a parallel session); this file only pins the flag, never re-tests the timing.
"""

from __future__ import annotations

from recon.findings import analyze, queries
from recon.findings import extract as extract_mod
from recon.findings.analyze import Coverage
from recon.findings.extract import extract


def _many_sinks(n: int) -> str:
    """A flat run of `n` resolvable sinks (~11 AST nodes each) — enough to exceed a tiny
    monkeypatched node budget without a slow-to-parse deep expression."""
    return "\n".join(f'fetch("/a{i}")' for i in range(n))


# --- extract(): the curtailed flag ------------------------------------------- #


def test_extract_not_curtailed_under_budget():
    # The default (6M) cap is far above any small input -> full recall, honestly "complete".
    result = extract(_many_sinks(50))
    assert result.curtailed is False
    assert len(result.endpoints) == 50


def test_extract_sets_curtailed_over_budget(monkeypatch):
    # Over a tiny cap, extract() bounds its expensive passes to a prefix AND records that it
    # did, so the dropped tail is surfaced (REQ-C2) instead of silently missing.
    src = _many_sinks(400)
    assert extract(src).curtailed is False  # control: the default cap leaves it complete
    monkeypatch.setattr(extract_mod, "_MAX_AST_NODES", 500)  # tiny cap -> bound to a prefix
    result = extract(src)
    assert result.curtailed is True
    assert 0 < len(result.endpoints) < 400  # partial recall, never a crash


# --- Coverage / merge propagation (REQ-C2 must survive the multi-asset roll-up) --- #


def test_merge_coverage_ors_curtailed():
    complete = Coverage(1, 0, 1)  # curtailed defaults False
    curtailed = Coverage(1, 0, 1, curtailed=True)
    assert analyze._merge_coverage(complete, complete).curtailed is False
    assert analyze._merge_coverage(complete, curtailed).curtailed is True
    assert analyze._merge_coverage(curtailed, complete).curtailed is True


def test_merge_coverage_payloads_ors_curtailed():
    # A run-wide merge is curtailed if ANY asset's coverage payload was; a payload predating
    # the field (no key) reads as not-curtailed, so old events stay honest by default.
    merged_true = queries._merge_coverage_payloads([{"curtailed": False}, {"curtailed": True}])
    assert merged_true["curtailed"] is True
    merged_false = queries._merge_coverage_payloads([{}, {"curtailed": False}])
    assert merged_false["curtailed"] is False


def test_coverage_view_reads_curtailed():
    assert queries._coverage_view_from_payload({"curtailed": True}).curtailed is True
    # An event predating the field defaults to not-curtailed (backward compat).
    assert queries._coverage_view_from_payload({}).curtailed is False

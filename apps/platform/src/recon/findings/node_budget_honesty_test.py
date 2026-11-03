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

from recon.findings import extract as extract_mod
from recon.findings.coverage import Coverage, merge
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


def test_run_total_is_curtailed_if_any_asset_was():
    complete, curtailed = Coverage(), Coverage(curtailed=True)
    assert merge([complete, complete]).curtailed is False
    assert merge([complete, curtailed]).curtailed is True
    assert merge([curtailed, complete]).curtailed is True


def test_old_events_read_as_not_curtailed_or_partial():
    assert Coverage.from_payload({"curtailed": True}).curtailed is True
    assert Coverage.from_payload({"recovered_partial": True}).recovered_partial is True
    # Events predating the fields default to complete (backward compat).
    assert Coverage.from_payload({}).curtailed is False
    assert Coverage.from_payload({}).recovered_partial is False


def test_run_total_recovery_is_partial_if_any_asset_was():
    # D37-L2: a run's map recovery is partial if ANY asset's was.
    assert merge([Coverage(), Coverage(recovered_partial=True)]).recovered_partial is True
    assert merge([Coverage(), Coverage()]).recovered_partial is False

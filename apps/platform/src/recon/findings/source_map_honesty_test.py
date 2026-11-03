"""Fast-lane tests for the D32 source-map-honesty status.

When a crawled/captured asset references an external ``//# sourceMappingURL=`` whose
``.map`` the fetch stage then FAILS to retrieve (oversized past the byte cap, 404,
blocked, malformed), the gap is no longer SILENT: the asset is flagged
``source_map_skipped`` and analyze reports coverage ``source_map:"skipped"`` — distinct
from a bundle that simply had no map (``"none"``). These tests pin the status logic and
its propagation through the merge / read-model glue.

Pure / no stack (mirrors ``node_budget_honesty_test.py``). The fetch-side flag+event and
the end-to-end analyze wiring are covered by the integration lane
(``fetch/fetch_multi_test.py``, ``findings/analyze_test.py``); this file pins only the
in-process honesty glue.
"""

from __future__ import annotations

from recon.findings import analyze
from recon.findings.coverage import Coverage, merge

# --- _analysis_units: the "skipped" vs "none" decision ----------------------- #


def test_analysis_units_skipped_when_referenced_map_missed():
    # A referenced map the fetch stage couldn't retrieve (source_map_skipped=True, no
    # stored ref / inline map) is the honest "skipped" gap — while STILL falling back to
    # bundle analysis (the same single unit a no-map bundle yields). D37-L2: _analysis_units
    # now returns an AnalysisUnits context manager, not a (units, status, count) tuple.
    with analyze._analysis_units(None, 'fetch("/api/x");', "capture", True) as units:
        assert units.source_map_status == "skipped"
        assert units.sources_recovered == 0
        assert units.is_bundle  # the lone bundle unit — the skip changes only the label
        assert units.names == [analyze._SOURCE_NAME]


def test_analysis_units_none_when_no_map_referenced():
    # No skip flag + no ref = a bundle that genuinely had no map -> "none", NOT "skipped",
    # so we never over-report a coverage gap that does not exist.
    with analyze._analysis_units(None, 'fetch("/api/x");', "capture", False) as units:
        assert units.source_map_status == "none"


# --- merge / read-model propagation (REQ-C2 must survive the roll-up) -------- #


def test_run_total_source_map_is_skipped_if_any_asset_skipped():
    # A partial source-map recovery is never masked by a peer asset's clean value, whichever
    # asset came last.
    none, skipped = Coverage(), Coverage(source_map="skipped")
    assert merge([none, none]).source_map == "none"
    assert merge([none, skipped]).source_map == "skipped"
    assert merge([skipped, none]).source_map == "skipped"
    # No skip anywhere -> the newest asset's value stands.
    assert merge([Coverage(source_map="capture"), none]).source_map == "none"


def test_event_reads_source_map_skipped():
    assert Coverage.from_payload({"source_map": "skipped"}).source_map == "skipped"
    # An event predating the field defaults to "none" (backward compat).
    assert Coverage.from_payload({}).source_map == "none"

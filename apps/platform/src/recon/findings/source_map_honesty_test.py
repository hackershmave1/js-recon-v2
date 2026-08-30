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

from recon.findings import analyze, queries
from recon.findings.analyze import Coverage

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


def test_merge_coverage_skipped_dominates():
    # The in-process aggregate (analyze_run's return) must not lose "skipped" when the
    # skipped asset is not the last one merged — mirrors curtailed's OR.
    none = Coverage(1, 0, 1)  # source_map defaults "none"
    skipped = Coverage(1, 0, 1, source_map="skipped")
    assert analyze._merge_coverage(none, none).source_map == "none"
    assert analyze._merge_coverage(none, skipped).source_map == "skipped"
    assert analyze._merge_coverage(skipped, none).source_map == "skipped"  # non-last skip survives


def test_merge_coverage_payloads_skipped_dominates():
    # A run-wide roll-up reports "skipped" if ANY asset's map was skipped, so a partial
    # source-map recovery is never masked by a peer asset's clean value.
    merged = queries._merge_coverage_payloads([{"source_map": "none"}, {"source_map": "skipped"}])
    assert merged["source_map"] == "skipped"
    # No skip anywhere -> the highest-id (first) event's value stands in.
    merged_clean = queries._merge_coverage_payloads(
        [{"source_map": "capture"}, {"source_map": "none"}]
    )
    assert merged_clean["source_map"] == "capture"


def test_coverage_view_reads_source_map_skipped():
    assert queries._coverage_view_from_payload({"source_map": "skipped"}).source_map == "skipped"
    # An event predating the field defaults to "none" (backward compat).
    assert queries._coverage_view_from_payload({}).source_map == "none"

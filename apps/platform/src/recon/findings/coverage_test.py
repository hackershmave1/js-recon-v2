"""The coverage record through its interface — pure, no stack (``read_run_coverage`` is covered by
the integration tests in queries_reveal_redaction_test / the analyze tests)."""

from __future__ import annotations

from recon.findings.coverage import Coverage, FileCoverage, merge


def test_payload_round_trips():
    full = Coverage(
        attributed=3,
        unattributed=1,
        secrets=2,
        secrets_suspected=4,
        internal_ips=5,
        secrets_engine="unavailable",
        sources_recovered=6,
        source_map="capture",
        curtailed=True,
        recovered_partial=True,
        files=(FileCoverage(path="a.js", attributed=3, unattributed=1),),
    )
    assert Coverage.from_payload(full.to_payload()) == full


def test_an_event_predating_fields_reads_honest_defaults():
    old = Coverage.from_payload({"attributed": 1, "files": [{"path": "x.js"}]})
    assert old == Coverage(
        attributed=1, files=(FileCoverage(path="x.js", attributed=0, unattributed=0),)
    )
    assert (old.secrets_engine, old.curtailed, old.recovered_partial) == ("ok", False, False)


def test_api_json_keeps_its_exact_shape():
    # The web Coverage type reads exactly these; the suspected / internal-IP counts are served
    # as finding-type counts elsewhere and must not leak in here.
    assert set(Coverage().to_json()) == {
        "attributed",
        "unattributed",
        "secrets",
        "secrets_engine",
        "sources_recovered",
        "source_map",
        "curtailed",
        "recovered_partial",
        "files",
    }


def test_event_payload_keeps_its_exact_shape():
    # The analyze.coverage payload is a durable event schema (and streamed over SSE).
    assert list(Coverage().to_payload()) == [
        "attributed",
        "unattributed",
        "secrets",
        "secrets_suspected",
        "internal_ips",
        "secrets_engine",
        "sources_recovered",
        "source_map",
        "curtailed",
        "recovered_partial",
        "files",
    ]


def test_merge_adds_counts_and_concatenates_files():
    a = Coverage(
        attributed=1,
        secrets=1,
        internal_ips=2,
        files=(FileCoverage(path="p", attributed=1, unattributed=0),),
    )
    b = Coverage(
        attributed=2,
        secrets_suspected=3,
        files=(FileCoverage(path="p", attributed=2, unattributed=0),),
    )
    total = merge([a, b])
    assert (total.attributed, total.secrets, total.secrets_suspected, total.internal_ips) == (
        3,
        1,
        3,
        2,
    )
    assert len(total.files) == 2  # same path from two blobs stays two entries


def test_merge_never_under_reports():
    ok, bad = (
        Coverage(),
        Coverage(secrets_engine="unavailable", curtailed=True, recovered_partial=True),
    )
    for parts in ([ok, bad], [bad, ok]):
        total = merge(parts)
        assert (total.secrets_engine, total.curtailed, total.recovered_partial) == (
            "unavailable",
            True,
            True,
        )


def test_merge_source_map_newest_wins_unless_one_was_skipped():
    assert merge([Coverage(source_map="inline"), Coverage(source_map="none")]).source_map == "none"
    skipped_first = [Coverage(source_map="skipped"), Coverage(source_map="inline")]
    assert merge(skipped_first).source_map == "skipped"


def test_merge_of_nothing_is_empty_coverage():
    assert merge([]) == Coverage()

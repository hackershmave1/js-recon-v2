"""Corpus regression tests: 13 synthetic JS fixtures vs the extractor.

Fixture corpus lives in ``example_js_files/`` at the repo root (not ``test-targets/`` —
those are crawl targets; these are direct-parse unit fixtures). Expected findings are
in ``expected.json`` using the three-axis taxonomy
(resolution / at_sink / scope / kind / base_source / bucket).

Three gates:
  1. **Precision — namespace suppression** (all 13 files): no XML-namespace / W3 / purl
     host must leak into extracted URLs.  The deny-list in ``extract._HARVEST_HOST_DENY``
     handles these; this test catches regressions.
  2. **Recall — template literal expansion** (fixture 01): the four at-sink URLs built by
     const-folding template literals must be extracted (fix shipped in commit 722846d).
  3. **Recall — axios instance** (fixture 06): the six resolved axios-instance URLs must be
     extracted.

Capability gaps documented in expected.json but NOT gated here (tracked in DEBT.md):
  wrapper-function call-graph (02), Angular HttpClient (04), Nuxt useFetch (05),
  dynamic URL assembly (07), route-table extraction (08), non-fetch sinks (09),
  third-party SDK config (10), obfuscation decode (12), service-worker precache (13),
  and at_sink=False (off-sink constant) emit path for all files.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from recon.findings.extract import extract

_PARENTS = pathlib.Path(__file__).parents
CORPUS_DIR = _PARENTS[5] / "example_js_files" if len(_PARENTS) > 5 else None
# The app image ships src/ only (shallower path, no repo-root fixtures); without this the
# module-level read below crashes collection and the whole integration lane runs nothing.
if CORPUS_DIR is None or not CORPUS_DIR.is_dir():
    pytest.skip(
        "source tree not present (installed image); covered by the host-tests lane",
        allow_module_level=True,
    )
_EXPECTED: dict[str, list[dict[str, object]]] = json.loads(
    (CORPUS_DIR / "expected.json").read_text(encoding="utf-8")
)["files"]

# Hosts that must never appear in extracted output: XML namespaces, schema registrars,
# W3 specs. These are blocked by _HARVEST_HOST_DENY + the NS-year-segment shape rule.
_BLOCKED_HOST_SUFFIXES = frozenset(
    {
        "w3.org",
        "purl.org",
        "purl.oclc.org",
        "openxmlformats.org",
        "schemas.microsoft.com",
        "oasis-open.org",
        "docbook.org",
        "ns.adobe.com",
    }
)


def _normalize(url: str) -> str:
    """Strip query string and normalize placeholder tokens for comparison.

    Extractor output may include runtime params in the URL (``?QueueId=${queueId}``)
    or use ``${id}``/``:id`` placeholder notation; expected.json uses ``{id}`` and no
    query string.  Strip and normalise so the comparison is about path coverage, not
    parameter encoding.
    """
    url = url.split("?")[0]
    url = re.sub(r"\$\{(\w+)\}", r"{\1}", url)       # ${varName} → {varName}
    url = re.sub(r":([a-zA-Z_]\w*)", r"{\1}", url)   # :varName  → {varName} (holder tokens)
    return url.rstrip("/")


def _all_extracted(source: str) -> set[str]:
    """Run extract() and return every URL from every lane, normalised."""
    result = extract(source)
    all_eps = result.endpoints + result.unresolved + result.generic + result.routes
    return {_normalize(ep.url) for ep in all_eps}


# ---------------------------------------------------------------------------
# Gate 1 — precision: namespace/boilerplate hosts must NOT appear (all 13 files)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", list(_EXPECTED.keys()))
def test_corpus_no_namespace_host_in_output(filename: str) -> None:
    """No XML-namespace or schema-registrar host must leak into extracted URLs."""
    source = (CORPUS_DIR / filename).read_text(encoding="utf-8")
    found = _all_extracted(source)
    leaked = {
        url
        for url in found
        if any(
            host in url
            for host in _BLOCKED_HOST_SUFFIXES
        )
    }
    assert not leaked, (
        f"[{filename}] namespace/boilerplate URLs leaked into extraction output:\n"
        + "\n".join(f"  {u}" for u in sorted(leaked))
    )


# ---------------------------------------------------------------------------
# Gate 2 — recall: template literal expansion (fixture 01, commit 722846d)
# ---------------------------------------------------------------------------

_BASE = "https://apigatewayazeu-dev.accenture.com/idvs/mfe/dev/v1.0"
_F01_AT_SINK = frozenset(
    {
        f"{_BASE}/AssignedQueue",
        f"{_BASE}/GDPR",
        f"{_BASE}/health",
        f"{_BASE}/AgenticAI/GetFilteredDocumentDetails",
    }
)


def test_corpus_fixture01_template_literal_at_sink_urls() -> None:
    """The four at-sink URLs in fixture 01 must be extracted via const-folded templates."""
    source = (CORPUS_DIR / "01-vite-react-baseurl-consts.js").read_text(encoding="utf-8")
    found = _all_extracted(source)
    missing = _F01_AT_SINK - found
    assert not missing, (
        "template-literal URLs missing from extraction:\n"
        + "\n".join(f"  {u}" for u in sorted(missing))
    )


# ---------------------------------------------------------------------------
# Gate 3 — recall: axios instance baseURL resolution (fixture 06)
# ---------------------------------------------------------------------------

_AUTH = "https://auth.accenture.com/oauth2/token/refresh"
_F06_AT_SINK = frozenset(
    {
        f"{_BASE}/AssignedQueue",
        f"{_BASE}/AssignedDocument",
        f"{_BASE}/Authentication",
        f"{_BASE}/Authentication/Client",
        f"{_BASE}/DocumentHistory",
        _AUTH,
    }
)


def test_corpus_fixture06_axios_instance_resolution() -> None:
    """All six axios-instance URLs in fixture 06 must be resolved and extracted."""
    source = (CORPUS_DIR / "06-axios-instance-baseurl.js").read_text(encoding="utf-8")
    found = _all_extracted(source)
    missing = _F06_AT_SINK - found
    assert not missing, (
        "axios-instance URLs missing from extraction:\n"
        + "\n".join(f"  {u}" for u in sorted(missing))
    )


# ---------------------------------------------------------------------------
# Gate 4 — recall: declared URL constants (at_sink=False) (fixture 01)
# ---------------------------------------------------------------------------

_F01_DECLARED = frozenset(
    {
        f"{_BASE}/DocumentDetailsPagination",
        f"{_BASE}/Authentication",
        f"{_BASE}/Authentication/AIG",
        f"{_BASE}/FieldHistory",
        # ApiUrl is the base prefix used as ${ApiUrl} in every other const — it is
        # intentionally excluded by the intermediate-prefix filter.
    }
)


def test_corpus_fixture01_declared_const_urls() -> None:
    """Five at_sink=False URL constants in fixture 01 must surface as declared findings."""
    source = (CORPUS_DIR / "01-vite-react-baseurl-consts.js").read_text(encoding="utf-8")
    result = extract(source)
    declared = {_normalize(ep.url) for ep in result.endpoints if not ep.at_sink}
    missing = _F01_DECLARED - declared
    assert not missing, (
        "declared URL constants missing from extraction:\n"
        + "\n".join(f"  {u}" for u in sorted(missing))
    )


# ---------------------------------------------------------------------------
# Informational: recall snapshot across all files (never fails, shows gaps)
# ---------------------------------------------------------------------------


def test_corpus_recall_snapshot(capsys: pytest.CaptureFixture[str]) -> None:
    """Print a recall table vs expected.json PROBE+NEEDS_HOST findings.

    This test never fails — it documents the current extraction ceiling and is the
    reference for tracking capability additions.  Run with ``-s`` to see the table.
    """
    rows: list[tuple[str, int, int]] = []
    for filename, findings in _EXPECTED.items():
        source = (CORPUS_DIR / filename).read_text(encoding="utf-8")
        found = _all_extracted(source)
        targets = [
            f
            for f in findings
            if f["bucket"] in ("PROBE", "NEEDS_HOST")
        ]
        hit = sum(1 for f in targets if _normalize(str(f["url"])) in found)
        rows.append((filename, hit, len(targets)))

    with capsys.disabled():
        print("\n--- corpus recall snapshot ---")
        total_hit = total_exp = 0
        for fname, hit, exp in rows:
            pct = f"{hit}/{exp}" if exp else "—"
            print(f"  {fname:<45} {pct}")
            total_hit += hit
            total_exp += exp
        print(f"  {'TOTAL':<45} {total_hit}/{total_exp}")

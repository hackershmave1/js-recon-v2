"""No-map deobfuscation — Phase 1: pure-Python beautify baseline.

When a bundle ships no source map, analyze falls back to the whole bundle under
``input.js``. A fully-minified bundle is ~one line, so every finding collapses to
line 1 and cannot be located. Beautifying the bundle BEFORE endpoint extraction
gives each finding a distinct, meaningful line, and ``recon.probe.sources`` serves
the same beautified text on demand so the finding marks align — the beautify is
deterministic (pinned options), so analyze and the Sources re-derive produce
byte-identical output with no persisted blob.

Pure-Python (``jsbeautifier``), in-process, FAIL-SOFT: over the input cap or on any
error it returns ``None`` and the caller uses the raw source unchanged. On the no-map
BUNDLE path, secrets scan the RAW bytes (``recon.probe.reveal`` slices the raw blob by
offset), so beautify there stays confined to endpoint extraction. The D32-B1 source-map-
RECOVERED path is different: analyze scans AND locates secrets in the SAME
``beautify_if_minified`` text ``recon.probe.sources``/``reveal`` reproduce, so both sides
agree byte-for-byte (D37-L2 streams that beautified tree to disk and scans it there).

Phase 2 (a ``webcrack`` opt-in engine that unpacks bundles into per-module files,
Node) plugs in behind this same fail-soft gate; not present yet.
"""

from __future__ import annotations

import jsbeautifier

from recon.observability import get_logger

log = get_logger("recon.findings.deobfuscate")

# Cap the beautifier INPUT so a giant bundle cannot stall the single worker thread:
# analyze heartbeats once per asset and the 30s stall window must not be blown by an
# in-process beautify. Over the cap -> soft-fail to the raw bundle (~1 MiB, well under
# both the 30s budget and the 10 MiB ingest cap so it actually triggers).
_MAX_BEAUTIFY_BYTES = 1024 * 1024

# Pinned options so analyze and the on-demand Sources re-derive produce BYTE-IDENTICAL
# text (finding line numbers must match the served source). 2-space matches the web viewer.
_OPTS = jsbeautifier.default_options()
_OPTS.indent_size = 2


def beautify(source: str) -> str | None:
    """Reformat a minified no-map bundle to readable, deterministic text.

    Returns ``None`` (caller falls back to the raw bundle unchanged) when the input
    exceeds the cap or beautification raises on pathological input."""
    if len(source) > _MAX_BEAUTIFY_BYTES:
        return None
    try:
        out: str = jsbeautifier.beautify(source, _OPTS)
    except Exception as exc:  # jsbeautifier can choke on pathological input — fail soft
        log.warning("deobfuscate.failed", error=str(exc))
        return None
    return out


# A source is "minified" when a line runs absurdly long — a bundle (or a vendor lib
# shipped minified in a source map's ``sourcesContent``) is one giant line. 500 matches
# the web viewer's ``isMinified()`` so client and server agree which files get
# pretty-printed, and only the FIRST lines are scanned (via ``find``, no substring
# allocation) so a genuinely multi-line original — the common recovered-source case — is
# rejected cheaply without materializing its lines. Split on ``\n`` only, like the web
# viewer's ``split("\n")``, so the two stay in lockstep.
_MINIFIED_LINE_LEN = 500
_MINIFIED_SCAN_LINES = 200


def _is_minified(source: str) -> bool:
    start = 0
    for _ in range(_MINIFIED_SCAN_LINES):
        newline = source.find("\n", start)
        end = len(source) if newline == -1 else newline
        if end - start > _MINIFIED_LINE_LEN:
            return True
        if newline == -1:
            return False
        start = newline + 1
    return False


def beautify_if_minified(source: str) -> str:
    """Beautify a MINIFIED source; return a genuinely multi-line original unchanged.

    Source-map-recovered originals are usually real, readable code — but some vendor
    libraries ship minified ``sourcesContent``. Those get the same deterministic,
    line-distinct :func:`beautify` a no-map bundle does, so a finding lands on a
    meaningful line and ``recon.probe.sources`` serves matching text (analyze and the
    on-demand serve run this identically). A non-minified original is returned as-is so
    its real line numbers survive; over the cap / on failure the raw source is returned
    unchanged (fail-soft, via :func:`beautify`)."""
    if not _is_minified(source):
        return source
    beautified = beautify(source)
    return beautified if beautified is not None else source

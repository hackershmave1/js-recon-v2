"""No-map deobfuscation — Phase 1: pure-Python beautify baseline.

When a bundle ships no source map, analyze falls back to the whole bundle under
``input.js``. A fully-minified bundle is ~one line, so every finding collapses to
line 1 and cannot be located. Beautifying the bundle BEFORE endpoint extraction
gives each finding a distinct, meaningful line, and ``recon.probe.sources`` serves
the same beautified text on demand so the finding marks align — the beautify is
deterministic (pinned options), so analyze and the Sources re-derive produce
byte-identical output with no persisted blob.

Pure-Python (``jsbeautifier``), in-process, FAIL-SOFT: over the input cap or on any
error it returns ``None`` and the caller uses the raw bundle unchanged. Secrets are
NEVER beautified — Kingfisher scans the raw bytes and ``recon.probe.reveal`` slices
the raw blob by offset, so this stays confined to endpoint extraction.

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

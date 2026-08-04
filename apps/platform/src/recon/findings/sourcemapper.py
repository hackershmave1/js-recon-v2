"""Sourcemapper source-recovery adapter (out-of-process engine).

Recovers a bundle's original source files from its source map so the analyze
stage can attribute findings to real per-source paths (e.g. ``app/src/api.js``)
instead of the single-file ``input.js`` placeholder — sharpening REQ-D3 identity.

The map reaches us two ways this slice: an uploaded ``.map`` (preferred) or an
inline ``data:`` map embedded in the bundle. An *external* ``sourceMappingURL``
(a URL to fetch from the target) is out of scope until the fetch stage exists.

Sourcemapper facts (github.com/denandz/sourcemapper): ``-url`` accepts a local
map path; ``-output`` (required) recreates the source tree from the map's
``sources`` entries. It has no binary release — the image builds it with Go.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field

from recon.config import get_settings
from recon.findings import engines
from recon.observability import get_logger

log = get_logger("recon.findings.sourcemapper")

# `//# sourceMappingURL=...` (or the legacy `//@`). The value runs to whitespace.
_SOURCE_MAPPING_URL_RE = re.compile(r"//[#@]\s*sourceMappingURL=(\S+)")

# Single-map mode: sourcemapper exits 0 on success and log.Fatal (exit 1) on a
# bad/unparseable map. There is no partial success, so only 0 is acceptable — a
# bad map raises EngineError and fails/retries the stage rather than silently
# yielding an empty recovery.
_OK_RETURNCODES = (0,)


@dataclass(frozen=True)
class RecoveredFile:
    """One original source file recovered from the map. ``path`` is relative to
    the recovery root (the map's ``sources`` layout), used as the finding path."""

    path: str
    content: bytes


@dataclass(frozen=True)
class RecoveredSources:
    files: list[RecoveredFile] = field(default_factory=list)
    status: str = "ok"  # ok | unavailable
    origin: str = "none"  # uploaded | inline | none — how the map was obtained


def extract_inline_map(js: str) -> bytes | None:
    """Return the raw source-map bytes from an inline ``data:`` ``sourceMappingURL``
    comment, or ``None`` if there is none / it points at an external URL.

    Per the source-map convention the LAST ``sourceMappingURL`` comment wins.
    Handles both base64 and percent-encoded ``data:`` payloads; an external
    reference (``//# sourceMappingURL=app.js.map``) returns ``None`` because
    fetching it needs the (deferred) fetch stage."""
    matches = _SOURCE_MAPPING_URL_RE.findall(js)
    if not matches:
        return None
    url = matches[-1].strip()
    if not url.startswith("data:"):
        return None  # external reference — deferred to the fetch stage
    header, sep, data = url[len("data:") :].partition(",")
    if not sep:
        return None
    if ";base64" in header:
        try:
            payload = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return None
    else:
        # Non-base64 data: URI — a percent-encoded JSON payload.
        payload = urllib.parse.unquote(data).encode("utf-8")
    # A source map is a JSON object; reject obvious garbage here so it never
    # reaches the tool (a malformed inline map must not be able to fail the run).
    return payload if payload.lstrip().startswith(b"{") else None


def recover_sources(
    map_bytes: bytes,
    *,
    origin: str = "uploaded",
    bin_path: str | None = None,
    timeout_s: float | None = None,
    max_recovered_bytes: int | None = None,
) -> RecoveredSources:
    """Recover a bundle's original sources from ``map_bytes`` via Sourcemapper.

    Returns ``status="unavailable"`` (soft) if the binary is missing; a genuine
    engine failure (bad map) re-raises so the analyze stage fails/retries. Files
    are read back from an isolated temp dir; a recovered path that resolves
    outside it is skipped (defense-in-depth — the tool already clamps ``../``),
    and total recovered bytes are capped."""
    settings = get_settings()
    bin_path = bin_path or settings.sourcemapper_bin
    timeout_s = timeout_s if timeout_s is not None else settings.engine_timeout_seconds
    cap = max_recovered_bytes if max_recovered_bytes is not None else settings.engine_max_output_bytes

    with tempfile.TemporaryDirectory(prefix="sm-") as workdir:
        map_path = os.path.join(workdir, "in.map")
        out_dir = os.path.join(workdir, "out")
        os.makedirs(out_dir, exist_ok=True)
        with open(map_path, "wb") as handle:
            handle.write(map_bytes)
        # -output is REQUIRED: without it sourcemapper prints usage, exits 0, and
        # writes nothing — so always pass it and treat an empty tree as "none".
        argv = [engines.resolve_bin(bin_path), "-url", map_path, "-output", out_dir]
        try:
            engines.run_engine(
                argv, timeout_s=timeout_s, max_output_bytes=cap, ok_returncodes=_OK_RETURNCODES
            )
        except engines.EngineNotAvailable:
            log.warning("sourcemapper.unavailable", bin=bin_path)
            return RecoveredSources(status="unavailable", origin=origin)
        files = _walk_recovered(out_dir, cap)

    log.info("sourcemapper.done", recovered=len(files), origin=origin)
    return RecoveredSources(files=files, status="ok", origin=origin)


def _walk_recovered(out_dir: str, cap: int) -> list[RecoveredFile]:
    root = os.path.realpath(out_dir)
    files: list[RecoveredFile] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(out_dir, followlinks=False):
        # Total, stable traversal order so the set kept under the byte cap is
        # deterministic across retries (os.walk's native dir order is not) — keeps
        # the finding-hash set identical on re-analysis (REQ-A3 idempotency).
        dirnames.sort()
        for name in sorted(filenames):
            abspath = os.path.join(dirpath, name)
            real = os.path.realpath(abspath)
            # Containment: never read a file that resolves outside the temp root.
            if real != root and not real.startswith(root + os.sep):
                log.warning("sourcemapper.escaped_path", path=name)
                continue
            with open(abspath, "rb") as handle:
                content = handle.read(cap - total + 1)
            total += len(content)
            if total > cap:
                log.warning("sourcemapper.truncated", cap=cap)
                return files
            rel = os.path.relpath(abspath, out_dir).replace(os.sep, "/")
            files.append(RecoveredFile(path=rel, content=content))
    return files

"""Sourcemapper source-recovery adapter (out-of-process engine).

Recovers a bundle's original source files from its source map so the analyze
stage can attribute findings to real per-source paths (e.g. ``app/src/api.js``)
instead of the single-file ``input.js`` placeholder — sharpening REQ-D3 identity.

The map reaches us three ways: an uploaded ``.map`` (preferred), an inline
``data:`` map embedded in the bundle (``extract_inline_map``), or an *external*
``//# sourceMappingURL=<url>`` reference. The external case is handled by the
fetch stage: ``external_map_url`` locates the reference and the fetcher GETs the
``.map`` through the egress guard, links it to the asset, and analyze recovers
sources with the tolerant ``"capture"`` origin (REQ-CE2).

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


def external_map_url(js: str) -> str | None:
    """Return the URL from an *external* ``//# sourceMappingURL=<url>`` comment, or
    ``None`` if there is none or it is an inline ``data:`` payload.

    Companion to :func:`extract_inline_map` for the fetch stage (REQ-CE2): that
    function returns the bytes of an *inline* map and ``None`` for an external ref;
    this one returns the external ref (and ``None`` for inline/absent) so the
    fetcher can GET it. Same last-match-wins convention as the inline path. The
    caller resolves the (possibly relative) ref against the asset URL and fetches
    it through the egress guard."""
    matches = _SOURCE_MAPPING_URL_RE.findall(js)
    if not matches:
        return None
    url: str = matches[-1].strip()
    if not url or url.startswith("data:"):
        return None
    return url


def recover_sources(
    map_bytes: bytes,
    *,
    origin: str = "uploaded",
    bin_path: str | None = None,
    timeout_s: float | None = None,
    max_recovered_bytes: int | None = None,
    memory_limit_bytes: int | None = None,
) -> RecoveredSources:
    """Recover a bundle's original sources from ``map_bytes`` via Sourcemapper.

    Returns ``status="unavailable"`` (soft) if the binary is missing; a genuine
    engine failure (bad map, or the D37-L0 memory ceiling tripping on an over-size
    map) re-raises as ``EngineError`` so the analyze stage's per-origin fallback
    decides (a crawl/inline map falls back to bundle analysis; an uploaded map
    surfaces). Files are read back from an isolated temp dir; a recovered path that
    resolves outside it is skipped (defense-in-depth — the tool already clamps
    ``../``), and total recovered bytes are capped. The recovery child's virtual
    memory is bounded (``memory_limit_bytes`` -> RLIMIT_AS, DEBT D37-L0) so a large
    map fails contained rather than OOM-ing the box."""
    settings = get_settings()
    bin_path = bin_path or settings.sourcemapper_bin
    timeout_s = timeout_s if timeout_s is not None else settings.engine_timeout_seconds
    cap = (
        max_recovered_bytes if max_recovered_bytes is not None else settings.engine_max_output_bytes
    )
    mem_limit = (
        memory_limit_bytes
        if memory_limit_bytes is not None
        else settings.sourcemapper_memory_limit_bytes
    )

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
                argv,
                timeout_s=timeout_s,
                max_output_bytes=cap,
                ok_returncodes=_OK_RETURNCODES,
                memory_limit_bytes=mem_limit,
            )
        except engines.EngineNotAvailable:
            log.warning("sourcemapper.unavailable", bin=bin_path)
            return RecoveredSources(status="unavailable", origin=origin)
        files = _walk_recovered(out_dir, cap)

    log.info("sourcemapper.done", recovered=len(files), origin=origin)
    return RecoveredSources(files=files, status="ok", origin=origin)


def recover_one_file(
    map_path: str,
    target_path: str,
    *,
    bin_path: str | None = None,
    timeout_s: float | None = None,
    memory_limit_bytes: int | None = None,
) -> bytes | None:
    """Recover ONE original (``target_path``) from the source map at ``map_path`` — a
    LOCAL file, so the caller never holds the map in memory (D37-L2 slice 2). Returns the
    file's raw bytes, or ``None`` if the map recovered nothing / has no such file / the
    binary is absent. Raises ``engines.EngineError`` on an unparseable map, exactly like
    :func:`recover_sources`, so a caller (the Sources viewer / audited reveal) picks its
    own fallback.

    Sourcemapper has no single-file mode, so it still writes the whole tree to a temp dir
    and the Go child still whole-loads the map — but the child is bounded (D37-L0
    ``prlimit``) and the tree is input-cap-bounded, and only the ONE requested file is read
    back into the caller's (API) process, not the whole ``list[RecoveredFile]``. This is
    the read the viewer/reveal do on a click, so it NARROWS the API parent's footprint from
    the whole recovered tree to a single file — it does NOT eliminate the vector: one
    map-sized ``sourcesContent`` entry can still load ~its own size (worst case ≈ the map
    input cap). A hard per-file read cap is the deferred M5 follow-up."""
    settings = get_settings()
    bin_path = bin_path or settings.sourcemapper_bin
    timeout_s = timeout_s if timeout_s is not None else settings.engine_timeout_seconds
    mem_limit = (
        memory_limit_bytes
        if memory_limit_bytes is not None
        else settings.sourcemapper_memory_limit_bytes
    )
    with tempfile.TemporaryDirectory(prefix="sm1-") as workdir:
        out_dir = os.path.join(workdir, "out")
        os.makedirs(out_dir, exist_ok=True)
        argv = [engines.resolve_bin(bin_path), "-url", map_path, "-output", out_dir]
        try:
            engines.run_engine(
                argv,
                timeout_s=timeout_s,
                max_output_bytes=settings.engine_max_output_bytes,
                ok_returncodes=_OK_RETURNCODES,
                memory_limit_bytes=mem_limit,
            )
        except engines.EngineNotAvailable:
            log.warning("sourcemapper.unavailable", bin=bin_path)
            return None
        return _read_recovered_file(out_dir, target_path)


def _read_recovered_file(out_dir: str, target_path: str) -> bytes | None:
    """Read ONE recovered file by its relative ``target_path`` from ``out_dir`` (a
    ``"/"``-separated path as recorded on a finding), or ``None`` if it is absent.
    Containment: a path that resolves outside the recovery root is refused (defense-in-
    depth — the same realpath guard :func:`_walk_recovered` applies to the whole tree)."""
    root = os.path.realpath(out_dir)
    abspath = os.path.join(out_dir, *target_path.split("/"))
    real = os.path.realpath(abspath)
    if real != root and not real.startswith(root + os.sep):
        log.warning("sourcemapper.escaped_path", path=target_path)
        return None
    if not os.path.isfile(real):
        return None
    with open(real, "rb") as handle:
        return handle.read()


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

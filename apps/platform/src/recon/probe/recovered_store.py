"""Stored source-map-recovered originals: the served text of every original analyze rebuilds from a
bundle's source map, plus ONE list per bundle (``{t}/{r}/originals/{bundle digest}``) mapping each
original's identity path to its stored copy.

Analyze rebuilds the whole recovered tree for each mapped bundle and used to throw it away, so the
viewer re-ran source-map recovery (a Go subprocess over the whole map) to open one original, and
search could only cover the few originals listed because they carry findings. With the text kept:
the viewer and search read originals like bundles — object-store reads, parallel and cancellable,
exact lines even in minified vendor files (stored as analyze beautified them).

Each entry keeps two names. ``path`` is the IDENTITY findings and the tree use
(``normalize_source_path``: content hashes collapsed, ``app.9f8e7d6c.js`` -> ``app.{hash}.js``);
``rel`` is the real name in the map. Opening by identity used to look the identity up on disk and
404. Two originals can share an identity; both stay searchable, and opening it serves the first
(as the tree only shows one node per identity).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from recon import storage
from recon.findings import normalize
from recon.observability import get_logger

if TYPE_CHECKING:
    from recon.probe.sources import SourceContent

_logger = get_logger(__name__)


@dataclass(frozen=True)
class Entry:
    path: str  # identity: what findings, the tree and search results use
    rel: str  # the original's real name inside the source map
    key: str  # its stored served text (a `formatted` blob)


@dataclass(frozen=True)
class Manifest:
    entries: list[Entry]
    partial: bool  # recovery stopped at its byte budget: some originals were never rebuilt


def manifest_key(bundle_key: str) -> str:
    tenant, run, _kind, digest = bundle_key.split("/")
    return f"{tenant}/{run}/originals/{digest}"


def store(bundle_key: str, files: Iterable[tuple[str, str]], *, partial: bool) -> None:
    """Store each ``(rel, served text)`` and the bundle's list. Best-effort: any failure is logged,
    never raised — analyze must not fail over it, and the viewer falls back to recovering."""
    from recon.probe.sources import _content_from_text

    tenant, run, _kind, _digest = bundle_key.split("/")
    try:
        entries = []
        for rel, text in files:
            path = normalize.normalize_source_path(rel)
            content = _content_from_text(path, text, formatted=True)
            payload = {
                "content": content.content,
                "truncated": content.truncated,
                "formatted": True,
            }
            key = storage.put_blob(
                tenant, run, "formatted", json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            entries.append({"path": path, "rel": rel, "key": key})
        body = {"entries": entries, "partial": partial}
        storage.put_blob_at(
            manifest_key(bundle_key), json.dumps(body, ensure_ascii=False).encode("utf-8")
        )
    except Exception:
        _logger.warning("recovered_store.store_failed", bundle_key=bundle_key, exc_info=True)


def load(bundle_key: str) -> Manifest | None:
    """The bundle's list of stored originals, or ``None`` (not stored yet / unreadable)."""
    try:
        body = json.loads(storage.get_blob(manifest_key(bundle_key)))
        return Manifest(
            entries=[Entry(path=e["path"], rel=e["rel"], key=e["key"]) for e in body["entries"]],
            partial=bool(body.get("partial", False)),
        )
    except Exception:
        return None


def content(bundle_key: str, path: str) -> SourceContent | None:
    """The stored served text of the original ``path`` (identity) of this bundle, or ``None``."""
    manifest = load(bundle_key)
    if manifest is None:
        return None
    entry = next((e for e in manifest.entries if e.path == path), None)
    return None if entry is None else read(entry.key, path=path)


def read(key: str, *, path: str) -> SourceContent | None:
    from recon.probe.sources import SourceContent

    try:
        payload = json.loads(storage.get_blob(key))
        return SourceContent(
            path=path,
            content=str(payload["content"]),
            truncated=bool(payload["truncated"]),
            formatted=bool(payload["formatted"]),
        )
    except Exception:
        return None

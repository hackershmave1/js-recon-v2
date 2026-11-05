"""A source blob's SERVED text (beautified once, or raw when beautify is over its cap / failed),
stored beside the input blob under the same digest so the viewer and search never re-beautify.

Beautifying one minified bundle costs seconds of pure-Python CPU; search used to redo it for every
file on every query (56 of ~60s on a 211-file run). Analyze already beautifies each bundle once to
locate findings, so it stores that exact text here, and the stored copy is what the viewer and search
read: finding marks stay aligned because nobody re-derives the text with a different formatter.

The key is ``{tenant}/{run}/formatted/{digest}`` where ``digest`` is the input blob's own content
hash (its key already ends in it), so a reader needs only the input key — no raw download — and the
run-prefix purge sweeps it with the run. A missing, unreadable or unwritable copy is never an error:
callers fall back to deriving the text, exactly as before this cache existed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from recon import storage
from recon.observability import get_logger

if TYPE_CHECKING:
    from recon.probe.sources import SourceContent

_logger = get_logger(__name__)


def formatted_key(input_key: str) -> str:
    """``{t}/{r}/<input kind>/{digest}`` -> ``{t}/{r}/formatted/{digest}``."""
    tenant, run, _kind, digest = input_key.split("/")
    return f"{tenant}/{run}/formatted/{digest}"


def store(input_key: str, content: SourceContent) -> None:
    """Best-effort write of the served text (already capped by the caller). Any failure — a
    ClientError, an EndpointConnectionError (not a ClientError), anything — is logged, never
    raised: losing this copy only costs a re-derive later, while raising would fail analysis."""
    payload = {
        "content": content.content,
        "truncated": content.truncated,
        "formatted": content.formatted,
    }
    try:
        storage.put_blob_at(formatted_key(input_key), json.dumps(payload).encode("utf-8"))
    except Exception:
        _logger.warning("formatted_text.store_failed", input_key=input_key, exc_info=True)


def store_served(input_key: str, text: str, *, formatted: bool) -> None:
    """Store analyze's served text, capped exactly as the viewer caps what it serves (a beautified
    nest can balloon far past the input), so a stored copy is never larger than a response."""
    from recon.probe.sources import _content_from_text

    store(input_key, _content_from_text("", text, formatted=formatted))


def load(input_key: str, *, path: str) -> SourceContent | None:
    """The stored served text for ``input_key``, or ``None`` (absent, unreadable, corrupt)."""
    from recon.probe.sources import SourceContent

    try:
        payload = json.loads(storage.get_blob(formatted_key(input_key)))
        return SourceContent(
            path=path,
            content=str(payload["content"]),
            truncated=bool(payload["truncated"]),
            formatted=bool(payload["formatted"]),
        )
    except Exception:
        return None

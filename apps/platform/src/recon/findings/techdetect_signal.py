"""Load a run's tech-detection fingerprint signal: the MERGE of every signal blob it has.

Producers write host-keyed ``fingerprint-signal`` blobs indexed by ``fingerprint.signal`` events:
one per crawl fetch call (a redelivered call writes a second, partial one), one per server-side
capture, and one per extension upload batch (DEBT D77). Reading only the latest would keep just the
last batch's hosts, so every blob is merged: hosts unioned, ``headers`` later-wins, the list fields
(scripts/meta/cookies) order-preserving unions.

Two bounds keep a long capture (thousands of batches, analyzed again on every incremental pass)
cheap and honest:
- GROWTH budget. ``MAX_SIGNAL_BYTES`` caps what the merge ADDS, not the raw sum: each batch repeats
  its host's header set, so a raw-sum cap would fill with duplicates and starve the newest batches.
  Once spent, the remaining blobs are not even downloaded (one warning).
- Compaction. A pass that merged new blobs stores the result as a ``fingerprint.merged`` blob that
  names every ref it covers; the next pass starts from it and reads only refs not yet covered. The
  covered set is explicit (not an event-id watermark) because a batch's event can commit after a
  higher-numbered one, and a watermark would skip it forever.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from recon import storage
from recon.db.base import tenant_session
from recon.db.models import RunEvent
from recon.events.log import record_event
from recon.observability import get_logger

log = get_logger("recon.findings.techdetect_signal")

MAX_SIGNAL_BYTES = 2_000_000  # the merged signal's growth budget
_MAX_BLOB_BYTES = 2_000_000  # read cap on one producer blob
# A merged blob holds up to the budget plus the last blob merged past it, plus its ref list.
_MAX_MERGED_BYTES = 4 * _MAX_BLOB_BYTES

_SIGNAL_EVENT = "fingerprint.signal"
_MERGED_EVENT = "fingerprint.merged"
_LIST_FIELDS = ("scripts", "meta", "cookies")


class SignalMerger:
    """Accumulates host-keyed signals, counting the bytes each merge actually adds."""

    def __init__(self) -> None:
        self.signal: dict[str, dict[str, Any]] = {}
        self.added_bytes = 0

    def add(self, blob: Any) -> None:
        """Fold one blob in. A malformed host entry or field is skipped: the blob is ours, but
        the extension's half of it came from an untrusted client."""
        if not isinstance(blob, dict):
            return
        for host, host_signal in blob.items():
            if not isinstance(host, str) or not isinstance(host_signal, dict):
                continue
            entry = self.signal.setdefault(
                host, {"headers": {}, "scripts": [], "meta": [], "cookies": []}
            )
            headers = host_signal.get("headers")
            if isinstance(headers, dict):
                for name, value in headers.items():
                    if entry["headers"].get(name) != value:
                        entry["headers"][name] = value
                        self.added_bytes += len(str(name)) + len(str(value))
            for field in _LIST_FIELDS:
                values = host_signal.get(field)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if value not in entry[field]:
                        entry[field].append(value)
                        self.added_bytes += len(str(value))


def merge_signals(blobs: list[Any]) -> dict[str, dict[str, Any]]:
    """Merge host-keyed signals, oldest first (no budget)."""
    merger = SignalMerger()
    for blob in blobs:
        merger.add(blob)
    return merger.signal


def load_fingerprint_signal(tenant_id: str, run_id: str) -> dict[str, Any]:
    """The run's merged signal as ``{host: HostSignal}`` (``{}`` if none is visible), compacting
    what it newly merged so the next pass starts from it."""
    signal_refs, merged_ref = _signal_events(tenant_id, run_id)
    merger = SignalMerger()
    covered: set[str] = set()
    if merged_ref is not None:
        base = _read_json(merged_ref, run_id, cap=_MAX_MERGED_BYTES)
        if isinstance(base, dict):
            merger.add(base.get("signal"))
            covered = {r for r in base.get("refs") or [] if isinstance(r, str)}
    pending = [ref for ref in dict.fromkeys(signal_refs) if ref not in covered]
    read: list[str] = []
    for ref in pending:
        if merger.added_bytes >= MAX_SIGNAL_BYTES:
            log.warning(
                "analyze.fingerprint_signal_budget_spent",
                run_id=run_id,
                merged_bytes=merger.added_bytes,
                unread_blobs=len(pending) - len(read),
            )
            break
        merger.add(_read_json(ref, run_id, cap=_MAX_BLOB_BYTES))
        read.append(ref)
    if read and (merged_ref is not None or len(read) > 1):
        _store_merged(tenant_id, run_id, merger.signal, sorted(covered | set(read)))
    return merger.signal


def _signal_events(tenant_id: str, run_id: str) -> tuple[list[str], str | None]:
    """Every ``fingerprint.signal`` ref (oldest first) and the latest ``fingerprint.merged`` ref."""
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            select(RunEvent.type, RunEvent.payload)
            .where(
                RunEvent.run_id == str(run_id),
                RunEvent.type.in_((_SIGNAL_EVENT, _MERGED_EVENT)),
            )
            .order_by(RunEvent.id)
        ).all()
    refs: list[str] = []
    merged_ref: str | None = None
    for event_type, payload in rows:
        ref = payload.get("signal_ref") if isinstance(payload, dict) else None
        if not isinstance(ref, str) or not ref:
            continue
        if event_type == _MERGED_EVENT:
            merged_ref = ref
        else:
            refs.append(ref)
    return refs, merged_ref


def _read_json(ref: str, run_id: str, *, cap: int) -> Any:
    """One stored blob's JSON, or ``None`` when it is over ``cap``."""
    raw = storage.get_blob(ref)
    if len(raw) > cap:
        log.warning("analyze.fingerprint_signal_oversized", run_id=run_id, bytes=len(raw))
        return None
    return json.loads(raw.decode("utf-8"))


def _store_merged(tenant_id: str, run_id: str, signal: dict[str, Any], refs: list[str]) -> None:
    """Persist the merge + the refs it covers. Recorded, not published (no live-feed reader)."""
    body = json.dumps({"signal": signal, "refs": refs}).encode("utf-8")
    merged_ref = storage.put_blob(tenant_id, run_id, "fingerprint-signal", body)
    with tenant_session(tenant_id) as session:
        record_event(
            session,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type=_MERGED_EVENT,
            payload={"signal_ref": merged_ref, "covers": len(refs)},
        )

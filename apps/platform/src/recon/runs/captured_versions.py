"""Extension ingest's captured-file rows: every version of a re-captured url is kept.

The extension re-uploads a url whose content changed (a deploy, a per-request config script).
Each file follows one rule, in upload order: the url's row is free -> it becomes the row; it
already holds these bytes -> a retry, nothing to do; it holds DIFFERENT bytes -> the bytes go to
the version row ``url#sha256=<hash>`` — the form browser capture gives same-url scripts
(capture/stage._asset_rows). Unlike browser capture, which suffixes every copy, the bare url here
is the FIRST version captured: it was committed before the next arrived.

Rows are born ``fetch_ok`` with their source map (or D32 skip flag) through
``assets.seed_captured``, so none is ever PENDING without an ``input_ref`` and none is clobbered.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recon.db import models
from recon.runs.assets import seed_captured

VERSION_MARK = "#sha256="


@dataclass(frozen=True)
class CapturedAsset:
    """A captured file whose bytes are already in the blob store."""

    url: str
    input_ref: str  # content-addressed: the key ends with the content's SHA-256
    source_map_ref: str | None = None
    source_map_skipped: bool = False  # D32-A2: a map was sent but exceeded the cap


@dataclass(frozen=True)
class CapturedVersionsResult:
    total: int  # the run's asset count after this add (eventually consistent across batches)
    versions: list[str] = field(default_factory=list)  # version urls this add inserted
    capped: list[CapturedAsset] = field(default_factory=list)  # turned away: too many versions
    vanished: list[CapturedAsset] = field(default_factory=list)  # row deleted mid-batch (purge)


def version_url(url: str, input_ref: str) -> str:
    """The asset url for another version of ``url``. Stable across retries."""
    return f"{url}{VERSION_MARK}{input_ref.rsplit('/', 1)[-1]}"


def add_captured_versions(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    files: Sequence[CapturedAsset],
    max_versions: int,
) -> CapturedVersionsResult:
    """Add a batch of captured files in the caller's transaction (see the module docstring).

    At most ``max_versions`` version rows per url, so a script whose bytes change on every load
    cannot grow a run per page view. It is a soft cap: concurrent batches may overshoot it."""
    first_by_url: dict[str, CapturedAsset] = {}
    for f in files:
        first_by_url.setdefault(f.url, f)  # one row per url per INSERT, so its winner is defined
    inserted = seed_captured(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        rows=[_row(f, f.url) for f in first_by_url.values()],
    )
    rest = [f for f in files if not (first_by_url[f.url] is f and f.url in inserted)]
    held = _read_held_input_refs(
        session, run_id, {u for f in rest for u in (f.url, version_url(f.url, f.input_ref))}
    )
    version_counts = {url: _count_versions(session, run_id, url) for url in {f.url for f in rest}}
    admitted: dict[str, CapturedAsset] = {}
    capped: list[CapturedAsset] = []
    vanished: list[CapturedAsset] = []
    for f in rest:
        url = version_url(f.url, f.input_ref)
        if held.get(f.url) == f.input_ref or url in held or url in admitted:
            continue  # these bytes are already stored (a retry, or this batch's own copy)
        if f.url not in held:
            vanished.append(f)
            continue
        # NOTE: an existing row with no input_ref (a pending crawl row) also lands here and gets a
        # version row; extension rows are always born with one, so it is unreachable today.
        if version_counts[f.url] >= max_versions:
            capped.append(f)
            continue
        version_counts[f.url] += 1
        admitted[url] = f
    # One sorted insert for every new version: concurrent batches lock rows in the same order.
    added = seed_captured(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        rows=[_row(f, url) for url, f in admitted.items()],
    )
    total = session.scalar(
        select(func.count())
        .select_from(models.RunAsset)
        .where(models.RunAsset.run_id == str(run_id))
    )
    return CapturedVersionsResult(
        total=int(total or 0), versions=sorted(added), capped=capped, vanished=vanished
    )


def _row(f: CapturedAsset, url: str) -> dict:
    return {
        "url": url,
        "input_ref": f.input_ref,
        "source_map_ref": f.source_map_ref,
        "source_map_skipped": f.source_map_skipped,
    }


def _read_held_input_refs(session: Session, run_id: str, urls: set[str]) -> dict[str, str | None]:
    if not urls:
        return {}
    rows = session.execute(
        select(models.RunAsset.url, models.RunAsset.input_ref).where(
            models.RunAsset.run_id == str(run_id), models.RunAsset.url.in_(urls)
        )
    )
    return dict(rows.tuples().all())


def _count_versions(session: Session, run_id: str, url: str) -> int:
    count = session.scalar(
        select(func.count())
        .select_from(models.RunAsset)
        .where(
            models.RunAsset.run_id == str(run_id),
            # autoescape: "_" and "%" in a url are literal, not LIKE wildcards
            models.RunAsset.url.startswith(url + VERSION_MARK, autoescape=True),
        )
    )
    return int(count or 0)

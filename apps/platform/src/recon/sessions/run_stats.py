"""Per-run counts for the workspace run switcher (capture lifecycle, option D).

Each run shows the same "files" and "endpoints" numbers the Sessions card shows for a session's
latest run (``files_collected`` and the shown ``TOTAL_ENDPOINT_TYPES``, through the hidden-findings
overlay). So a large earlier capture stays visibly larger than a smaller later one. Each count scans
the run's occurrences (``visible_type_counts``), so only the newest ``LIMIT`` runs get numbers;
older ones read as unknown (``None``), never a faked zero."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from recon.db.models import Run
from recon.domain import TOTAL_ENDPOINT_TYPES
from recon.findings.queries import files_collected, visible_type_counts

LIMIT = 20


def run_stats(db: Session, runs: Sequence[Run]) -> dict[str, tuple[int, int]]:
    """``{run_id: (files, endpoints)}`` for the first ``LIMIT`` of ``runs`` (newest first)."""
    stats: dict[str, tuple[int, int]] = {}
    for run in runs[:LIMIT]:
        counts = visible_type_counts(db, str(run.id))
        endpoints = sum(int(counts.get(t.value, 0)) for t in TOTAL_ENDPOINT_TYPES)
        stats[str(run.id)] = (files_collected(db, run), endpoints)
    return stats

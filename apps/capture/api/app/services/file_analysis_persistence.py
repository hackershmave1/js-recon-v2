"""Race-safe persistence helpers for ``file_analyses`` rows.

``file_analyses.file_id`` is UNIQUE (see ``app/models/file_analysis.py``). Three
call sites analyze a file with the same non-atomic shape — ``SELECT by file_id``
then, on a miss, ``INSERT`` — so two workers analyzing the same file can both
SELECT-miss and both INSERT, and the loser raises a ``UniqueViolation``. This
helper centralises the get-or-create so every site recovers identically.
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import FileAnalysis as DbFileAnalysis


def get_or_create_analyzing_file_analysis(
    db: Session,
    file_id,
    session_id,
) -> DbFileAnalysis:
    """Return the file's ``FileAnalysis`` row set to ``analyzing``, creating it if absent.

    The INSERT is wrapped in a SAVEPOINT (``begin_nested``) so that a concurrent
    writer racing us on ``file_id`` degrades to adopting the winner's row instead
    of poisoning the caller's transaction with an unhandled ``IntegrityError``.
    Inert for the single-writer path — the nested flush simply succeeds.

    The row is flushed but not committed; the caller owns the commit.
    """
    row = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == file_id).first()
    if row is None:
        row = DbFileAnalysis(
            file_id=file_id,
            session_id=session_id,
            status="analyzing",
            analysis={},
            stats={},
            extractors_used=[],
            error=None,
        )
        try:
            # add() MUST be inside the SAVEPOINT: an object added before begin_nested()
            # stays in session.new after ROLLBACK TO SAVEPOINT, so on the race path the
            # caller's later commit() would re-INSERT this dead row and hit the unique
            # violation a second time. Adding inside means the nested rollback expunges it.
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # Lost the insert race: adopt the row the concurrent writer committed.
            row = db.query(DbFileAnalysis).filter(DbFileAnalysis.file_id == file_id).first()
            if row is None:
                # No winner visible (no delete path exists today) — don't hand back None.
                raise
            row.status = "analyzing"
            row.error = None
            row.updated_at = datetime.utcnow()
            db.flush()
    else:
        row.status = "analyzing"
        row.error = None
        row.updated_at = datetime.utcnow()
        db.flush()
    return row

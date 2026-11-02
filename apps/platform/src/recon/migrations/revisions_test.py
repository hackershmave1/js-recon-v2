"""Static guards on the Alembic revision graph — no DB needed, so they run in the fast lane.

Both failures below otherwise surface only when ``upgrade head`` runs against a live
Postgres (the integration lane), where they break every test at collection.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Postgres ``alembic_version.version_num`` is varchar(32): a longer id passes locally and
# then dies with StringDataRightTruncation on the UPDATE (hit 2026-08-23 by 0019).
MAX_REVISION_ID_LENGTH = 32


def _load_script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent))
    return ScriptDirectory.from_config(config)


def test_revision_ids_fit_alembic_version_column() -> None:
    too_long = [
        revision.revision
        for revision in _load_script_directory().walk_revisions()
        if len(revision.revision) > MAX_REVISION_ID_LENGTH
    ]
    assert too_long == [], f"revision ids over {MAX_REVISION_ID_LENGTH} chars: {too_long}"


def test_revision_graph_has_single_head() -> None:
    # Two parallel branches each adding NNNN_* off the same parent → `upgrade head` refuses.
    heads = _load_script_directory().get_heads()
    assert len(heads) == 1, f"multiple Alembic heads, set down_revision to the latest: {heads}"

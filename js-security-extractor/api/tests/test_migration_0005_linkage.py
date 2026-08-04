"""Loads the 0005 migration by file path and asserts its revision chain + that it
defines upgrade/downgrade. Pure-unit (executing the module only runs top-level
imports + assignments, not the op.* calls inside upgrade())."""
import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0005_projects.py"
_spec = importlib.util.spec_from_file_location("mig0005", _PATH)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def test_revision_chain():
    assert mig.revision == "0005"
    assert mig.down_revision == "0004"


def test_has_upgrade_and_downgrade():
    assert callable(mig.upgrade) and callable(mig.downgrade)

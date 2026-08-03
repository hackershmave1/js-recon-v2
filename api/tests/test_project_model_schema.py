"""Model-registration + column-shape checks. Imports the models package (which
attaches every model to Base.metadata) but never opens a DB connection, so it
runs in the pure-unit lane."""
from app.db import Base
from app import models  # noqa: F401  (importing registers models on Base.metadata)


def test_projects_table_registered():
    assert "projects" in Base.metadata.tables


def test_session_has_project_columns():
    cols = Base.metadata.tables["sessions"].columns
    assert "project_id" in cols
    assert "capture_config" in cols
    assert "override_keys" in cols


def test_project_id_is_fk_to_projects_with_set_null():
    fks = list(Base.metadata.tables["sessions"].c.project_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "projects"
    assert fks[0].ondelete == "SET NULL"

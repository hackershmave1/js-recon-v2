from sqlalchemy import CheckConstraint, UniqueConstraint

from recon.db import models
from recon.domain import BaseUrlRuleKind


def test_kind_enum_values():
    assert [m.value for m in BaseUrlRuleKind] == ["prefix", "selection"]


def test_session_base_url_table_shape():
    table = models.SessionBaseUrl.__table__
    assert table.name == "session_base_url"
    cols = set(table.columns.keys())
    assert {
        "id",
        "tenant_id",
        "session_id",
        "kind",
        "path_prefix",
        "finding_hashes",
        "base_url",
        "actor",
        "created_at",
        "updated_at",
    } <= cols
    # A unique (session_id, path_prefix) so prefix rules upsert; selection rows (NULL prefix) don't collide.
    assert any(
        isinstance(c, UniqueConstraint)
        and {col.name for col in c.columns} == {"session_id", "path_prefix"}
        for c in table.constraints
    )
    # The kind CHECK + the "exactly one match field per kind" CHECK both present.
    checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    assert any(c.name == "ck_base_url_kind" for c in checks)
    assert any(c.name == "ck_base_url_match_field" for c in checks)


def test_registered_for_rls():
    assert models.BASE_URL_TABLES == ("session_base_url",)

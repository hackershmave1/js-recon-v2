from recon.db import models


def test_spec_tables_registered_and_tenant_scoped():
    assert models.SPEC_TABLES == ("session_spec", "finding_spec_status")
    assert "tenant_id" in models.FindingSpecStatus.__table__.columns
    assert "tenant_id" in models.SessionSpec.__table__.columns


def test_finding_spec_status_unique_on_session_hash():
    uqs = {
        tuple(c.name for c in u.columns)
        for u in models.FindingSpecStatus.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("session_id", "finding_hash") in uqs


def test_spec_blob_kind_registered():
    from recon import storage

    assert "spec" in storage.BLOB_KINDS

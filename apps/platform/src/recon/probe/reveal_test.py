import json

import pytest
from botocore.exceptions import EndpointConnectionError

from recon import storage
from recon.db import models
from recon.db.base import tenant_session
from recon.domain import FindingType
from recon.findings import kingfisher, normalize, store
from recon.probe import reveal
from recon.sessions import service as sessions_service

pytestmark = pytest.mark.integration


def _new_run(tenant, session_id):
    with tenant_session(tenant) as session:
        run = models.Run(tenant_id=tenant, session_id=session_id, state="done")
        session.add(run)
        session.flush()
        return str(run.id)


def _set_input_ref(tenant, run_id, input_ref):
    with tenant_session(tenant) as session:
        session.get(models.Run, run_id).input_ref = input_ref


def _add_secret_finding(tenant, run_id, *, value, source, token, offsets="auto"):
    if offsets == "auto":
        offset_start = kingfisher.byte_offset(source, 1, source.index(token))
        offset_end = offset_start + len(token.encode("utf-8"))
    else:
        offset_start, offset_end = (None, None) if offsets is None else offsets
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=1, col=0,
                offset_start=offset_start, offset_end=offset_end, engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        return result.finding_hash


def _events(tenant, run_id, event_type):
    with tenant_session(tenant) as session:
        return (
            session.query(models.RunEvent)
            .filter_by(run_id=run_id, type=event_type)
            .all()
        )


def _seed(tenant, session_id, *, token, source=None, offsets="auto", value=None, input_ref="auto"):
    source = source if source is not None else f'const k = "{token}";\n'
    value = value if value is not None else normalize.normalize_secret_value(token, "stripe")
    run_id = _new_run(tenant, session_id)
    if input_ref == "auto":
        input_ref = storage.put_blob(tenant, run_id, "input", source.encode("utf-8"))
    _set_input_ref(tenant, run_id, input_ref)
    finding_hash = _add_secret_finding(
        tenant, run_id, value=value, source=source, token=token, offsets=offsets
    )
    return run_id, finding_hash


def test_reveal_happy_path_returns_value_and_audits_without_leaking():
    tenant = sessions_service.create_tenant("rv-1")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "PLAINTEXT000"
    run_id, finding_hash = _seed(tenant, session_id, token=token)

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash, actor="tester", reason="validate")
    assert outcome is not None and outcome.revealed is True
    assert outcome.value == token

    (event,) = _events(tenant, run_id, "secret.revealed")
    assert event.payload["finding_hash"] == finding_hash
    assert "value" not in event.payload
    assert token not in json.dumps(event.payload)  # audit never carries the secret


def test_reveal_aligns_offsets_through_invalid_utf8_bytes():
    # A stray non-UTF-8 byte before the token: analyze computed offsets on the
    # decode("utf-8","replace") string, so reveal must slice that same space.
    tenant = sessions_service.create_tenant("rv-2")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "MULTIBYTE00"
    raw = b"// \xff\nconst k = \"" + token.encode("utf-8") + b"\";\n"
    source = raw.decode("utf-8", "replace")
    line = 2
    col = source.split("\n")[1].index(token)
    offset = kingfisher.byte_offset(source, line, col)
    value = normalize.normalize_secret_value(token, "stripe")

    run_id = _new_run(tenant, session_id)
    input_ref = storage.put_blob(tenant, run_id, "input", raw)
    _set_input_ref(tenant, run_id, input_ref)
    with tenant_session(tenant) as session:
        result = store.record_finding(
            session, tenant_id=tenant, run_id=run_id, finding_type=FindingType.SECRET,
            value=value, path="input.js",
            occurrence=store.Occurrence(
                source_path="input.js", line=line, col=col,
                offset_start=offset, offset_end=offset + len(token.encode("utf-8")),
                engine="kingfisher",
            ),
            attributes={"rule": "stripe", "name": "Stripe"},
        )
        finding_hash = result.finding_hash

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is True and outcome.value == token


def test_reveal_integrity_mismatch_refuses_and_audits_denied():
    tenant = sessions_service.create_tenant("rv-3")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "REALTOKEN00"
    # Store the identity of a DIFFERENT token, so slicing the blob won't hash-match.
    wrong_value = normalize.normalize_secret_value("sk_live_OTHER", "stripe")
    run_id, finding_hash = _seed(tenant, session_id, token=token, value=wrong_value)

    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "integrity"
    assert reveal.DENIAL_STATUS["integrity"] == 409
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_missing_input_ref_is_source_gone():
    tenant = sessions_service.create_tenant("rv-4")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "X0", input_ref=None
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "source_gone"
    assert reveal.DENIAL_STATUS["source_gone"] == 410
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_purged_blob_is_source_gone():
    tenant = sessions_service.create_tenant("rv-5")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "Y0",
        input_ref="doesnotexist/run/input/deadbeef",
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "source_gone"


def test_reveal_offsetless_secret_is_denied():
    tenant = sessions_service.create_tenant("rv-6")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(
        tenant, session_id, token="sk_" + "live_" + "Z0", offsets=None
    )
    outcome = reveal.reveal_secret(tenant, run_id, finding_hash)
    assert outcome.revealed is False and outcome.denial == "no_offsets"
    assert reveal.DENIAL_STATUS["no_offsets"] == 422
    assert len(_events(tenant, run_id, "secret.reveal_denied")) == 1


def test_reveal_unexpected_blob_error_is_audited_then_reraised(monkeypatch):
    # An infra fault (e.g. a transient BotoCoreError, NOT a ClientError) reading the
    # blob is still a reveal ATTEMPT and must be audited (REQ-S3) even though the
    # caller still sees the original exception (the API surfaces it as a 500).
    tenant = sessions_service.create_tenant("rv-8")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    token = "sk_" + "live_" + "INFRAERR00"
    run_id, finding_hash = _seed(tenant, session_id, token=token)

    def _raise_infra_error(*args, **kwargs):
        raise EndpointConnectionError(endpoint_url="http://x")

    monkeypatch.setattr(storage, "get_blob", _raise_infra_error)

    with pytest.raises(EndpointConnectionError):
        reveal.reveal_secret(tenant, run_id, finding_hash, actor="tester", reason="validate")

    (event,) = _events(tenant, run_id, "secret.reveal_denied")
    assert event.payload["denial"] == "error"
    assert event.payload["finding_hash"] == finding_hash
    assert "value" not in event.payload
    assert token not in json.dumps(event.payload)


def test_reveal_unknown_or_other_tenant_returns_none_without_audit():
    tenant = sessions_service.create_tenant("rv-7")
    other = sessions_service.create_tenant("rv-7-other")
    session_id = sessions_service.create_session(
        tenant, name="e", scope_hosts=["acme.io"], authorized_by="t"
    ).id
    run_id, finding_hash = _seed(tenant, session_id, token="sk_" + "live_" + "Q0")

    assert reveal.reveal_secret(tenant, run_id, "f" * 64) is None  # no such finding
    assert reveal.reveal_secret(other, run_id, finding_hash) is None  # RLS: run invisible
    assert _events(tenant, run_id, "secret.reveal_denied") == []

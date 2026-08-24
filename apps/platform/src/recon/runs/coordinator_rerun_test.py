"""Host-lane unit tests for coordinator.edit_and_rerun (edit-&-re-run clone core).

The DB/session boundaries (get_run_config, create_session, start_run*) are mocked so
the SECURITY-critical logic — fork-on-scope-change with a FRESH ack (MF1/MF3), the
never-mutate/immutability path (D1), upload blob-copy (D5), the capture-precondition
re-check (MF6), and crawl_mode inheritance (D7) — is exercised without a live stack.
The full DB path is covered by the integration router tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from recon.runs import coordinator
from recon.runs.queries import RunConfigView
from recon.sessions import service as sessions_service

_MIB = 1024 * 1024


def _cfg(**over) -> RunConfigView:
    base = {
        "run_id": "run-1",
        "session_id": "sess-1",
        "target": "acme.io",
        "crawl_mode": None,
        "input_ref": None,
        "source_map_ref": None,
        "max_fetch_bytes": None,
        "scan_suspected_secrets": None,
        "scope_hosts": ["acme.io"],
        "engagement_id": "eng-1",
        "session_name": "Acme",
    }
    base.update(over)
    return RunConfigView(**base)


def _fake_create_session(tenant_id, *, authorized_by, **_k):
    # Mirror the real create_session's fresh-ack gate: a blank authorized_by is
    # rejected. This is what makes a scope fork re-attest authorization (MF1).
    if not authorized_by:
        raise sessions_service.AuthorizationRequired("an authorization acknowledgment is required")
    return SimpleNamespace(id="new-sess")


@contextmanager
def _patched(cfg, *, enable_capture=True, allow_local=False, blob=b"fetch('/x')"):
    view = SimpleNamespace(id="new-run", state="queued")
    with (
        patch("recon.runs.coordinator.run_queries.get_run_config", return_value=cfg),
        patch(
            "recon.runs.coordinator.get_settings",
            return_value=SimpleNamespace(
                enable_capture_mode=enable_capture, allow_local_egress=allow_local
            ),
        ),
        patch(
            "recon.runs.coordinator.sessions_service.create_session",
            side_effect=_fake_create_session,
        ) as create_session,
        patch("recon.runs.coordinator.start_run", return_value=view) as start_run,
        patch(
            "recon.runs.coordinator.start_run_with_input", return_value=view
        ) as start_run_with_input,
        patch("recon.runs.coordinator.storage.get_blob", return_value=blob) as get_blob,
    ):
        yield SimpleNamespace(
            create_session=create_session,
            start_run=start_run,
            start_run_with_input=start_run_with_input,
            get_blob=get_blob,
        )


def test_no_edits_reuses_session_and_carries_crawl_mode() -> None:
    # D7: a capture run re-runs AS capture (the old path silently dropped crawl_mode).
    with _patched(_cfg(crawl_mode="capture"), enable_capture=True) as m:
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1")
    m.create_session.assert_not_called()  # same scope + target in scope => no fork
    kwargs = m.start_run.call_args.kwargs
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["crawl_mode"] == "capture"
    assert kwargs["target"] == "acme.io"


def test_target_edit_within_scope_reuses_session() -> None:
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1", target="api.acme.io")
    m.create_session.assert_not_called()  # subdomain still in scope => no fork
    assert m.start_run.call_args.kwargs["session_id"] == "sess-1"
    assert m.start_run.call_args.kwargs["target"] == "api.acme.io"


def test_target_leaving_scope_forks_with_seeded_scope_and_fresh_ack() -> None:
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(
            MagicMock(),
            tenant_id="t",
            run_id="run-1",
            target="evil.example",
            authorized_by="tester",
        )
    ck = m.create_session.call_args.kwargs
    assert ck["scope_hosts"] == []  # seeded from the new target by create_session
    assert ck["target"] == "evil.example"
    assert ck["authorized_by"] == "tester"  # a FRESH ack, never the source session's
    assert ck["engagement_id"] == "eng-1"  # stays under the same engagement
    assert m.start_run.call_args.kwargs["session_id"] == "new-sess"


def test_scope_edit_forks_with_new_scope() -> None:
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(
            MagicMock(),
            tenant_id="t",
            run_id="run-1",
            scope_hosts=["acme.io", "cdn.acme.io"],
            authorized_by="tester",
        )
    assert m.create_session.call_args.kwargs["scope_hosts"] == ["acme.io", "cdn.acme.io"]
    assert m.start_run.call_args.kwargs["session_id"] == "new-sess"


def test_resubmitting_unchanged_scope_does_not_fork() -> None:
    # The UI re-sends the PREFILLED scope on every submit; an unchanged scope must NOT
    # force a fork or a fresh ack (value-compare, not mere presence).
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(
            MagicMock(), tenant_id="t", run_id="run-1", scope_hosts=["acme.io"]
        )
    m.create_session.assert_not_called()
    assert m.start_run.call_args.kwargs["session_id"] == "sess-1"


def test_fork_without_authorized_by_is_rejected() -> None:
    # MF1: changing scope with no fresh attestation must fail, never inherit the ack.
    with _patched(_cfg()), pytest.raises(sessions_service.AuthorizationRequired):
        coordinator.edit_and_rerun(
            MagicMock(), tenant_id="t", run_id="run-1", scope_hosts=["other.io"]
        )


def test_upload_source_copies_blob_and_target_never_forks() -> None:
    cfg = _cfg(input_ref="t/run-1/input/deadbeef", target="hint.notinscope", source_map_ref=None)
    with _patched(cfg) as m:
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1")
    m.create_session.assert_not_called()  # an upload never fetches its target => no fork
    m.start_run.assert_not_called()
    sk = m.start_run_with_input.call_args.kwargs
    assert sk["session_id"] == "sess-1"
    assert sk["js_source"] == b"fetch('/x')"  # the SAME stored bytes, copied
    assert sk["target"] == "hint.notinscope"  # the REQ-C2 base-URL hint, passed through


def test_capture_rerun_with_capture_disabled_fails_clean() -> None:
    # MF6: a clean 400-mapped error, not a silent static downgrade or a worker DLQ.
    with (
        _patched(_cfg(crawl_mode="capture"), enable_capture=False),
        pytest.raises(coordinator.CaptureModeUnavailable),
    ):
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1")


def test_capture_rerun_clearing_target_fails_clean() -> None:
    # A capture crawl_mode with no target can't open a page — clean 400, not a DLQ (MF6).
    with (
        _patched(_cfg(crawl_mode="capture"), enable_capture=True),
        pytest.raises(coordinator.CaptureModeUnavailable),
    ):
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1", target="")


def test_missing_source_run_raises_run_not_found() -> None:
    # MF4: an RLS miss (not visible to the tenant) is a 404, closing the IDOR.
    with _patched(None), pytest.raises(coordinator.RunNotFound):
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="nope")


def test_fetch_cap_edit_threads_to_start_run() -> None:
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(
            MagicMock(), tenant_id="t", run_id="run-1", max_fetch_bytes=20 * _MIB
        )
    assert m.start_run.call_args.kwargs["max_fetch_bytes"] == 20 * _MIB


def test_scan_suspected_edit_threads_to_start_run() -> None:
    # D33-B: opting into the suspected lane on a re-run threads the flag to the new run.
    with _patched(_cfg()) as m:
        coordinator.edit_and_rerun(
            MagicMock(), tenant_id="t", run_id="run-1", scan_suspected_secrets=True
        )
    assert m.start_run.call_args.kwargs["scan_suspected_secrets"] is True


def test_scan_suspected_is_inherited_when_not_edited() -> None:
    # Unset on the re-run → inherit the source run's setting (like every other field),
    # so a re-run of a suspected-lane run stays a suspected-lane run.
    with _patched(_cfg(scan_suspected_secrets=True)) as m:
        coordinator.edit_and_rerun(MagicMock(), tenant_id="t", run_id="run-1")
    assert m.start_run.call_args.kwargs["scan_suspected_secrets"] is True

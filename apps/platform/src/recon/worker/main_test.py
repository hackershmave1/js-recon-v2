"""Hermetic tests for worker routing + failure decisions (no infra).

The DB/queue/stage side effects (get_run_flags, transitions, claim/finish_job,
reschedule, to_dlq, ack, advance) are faked so the routing decisions — the
process_message state matrix and how a retry delay / DLQ is chosen — are tested in
isolation. The one live-infra path (a mid-stage ControlInterrupt driven through
run_once) stays below, integration-marked.
"""

from __future__ import annotations

import types

import pytest

from recon.domain import QueueName, RunStage, RunState
from recon.progress import heartbeat as progress
from recon.queue import retry, streams
from recon.runs import coordinator, queries
from recon.worker import main as worker


def _stub_side_effects(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: True)
    monkeypatch.setattr(streams, "ack", lambda *a, **k: None)
    monkeypatch.setattr(
        streams,
        "reschedule",
        lambda redis, queue, message, delay: captured.__setitem__("delay", delay),
    )
    return captured


def _handle(exc):
    return worker._handle_failure(
        None,
        QueueName.FETCH,
        "1-0",
        {"attempts": 0, "max_attempts": 5},
        exc,
        tenant_id="t",
        run_id="r",
        job_id="j",
        stage=RunStage.FETCHING,
        attempts=0,
        max_attempts=5,
    )


def test_retry_after_is_a_lower_bound_on_the_backoff(monkeypatch):
    # A politeness throttle asks for 30s; the sampled exponential backoff (<=1s for
    # attempt 1) must never undercut it (REQ-Q3).
    captured = _stub_side_effects(monkeypatch)
    result = _handle(retry.RetryableError("throttled", retry_after=30.0))
    assert result == "retry"
    assert captured["delay"] >= 30.0


def test_no_retry_after_uses_plain_backoff(monkeypatch):
    captured = _stub_side_effects(monkeypatch)
    result = _handle(retry.RetryableError("transient"))
    assert result == "retry"
    # attempt 1 backoff ceiling is base_delay (1.0); no artificial floor applied.
    assert 0.0 <= captured["delay"] <= 1.0


@pytest.mark.integration
def test_control_interrupt_pauses_without_advancing(monkeypatch, redis, authorized_session):
    tenant, session_id = authorized_session
    view = coordinator.start_run(redis, tenant_id=tenant, session_id=session_id, target="acme.io")
    # Drive the discover message; make the stage raise a cancel interrupt.
    monkeypatch.setattr(
        worker,
        "_run_stage_work",
        lambda *a, **k: (_ for _ in ()).throw(retry.ControlInterrupt("cancel")),
    )
    advanced = {"n": 0}
    monkeypatch.setattr(
        coordinator, "advance", lambda *a, **k: advanced.__setitem__("n", advanced["n"] + 1)
    )

    processed = worker.run_once(redis, "worker-test", block_ms=50)

    assert processed >= 1
    assert advanced["n"] == 0  # a cancel must NOT advance the run
    assert queries.get_run_flags(tenant, view.id).state == "cancelled"


# ---------------------------------------------------------------------------
# process_message routing matrix (hermetic — every collaborator is faked)
# ---------------------------------------------------------------------------


def _flags(state: str = "discovering", *, cancel: bool = False, pause: bool = False):
    return types.SimpleNamespace(state=state, cancel_requested=cancel, pause_requested=pause)


def _wire(
    monkeypatch,
    *,
    top_flags,
    loop_flags=None,
    claim: bool = True,
    enter: bool = True,
    finish: bool = True,
    stage_work=None,
) -> dict:
    """Fake every side-effecting collaborator of process_message and capture the
    observable outcomes (transitions fired, advance, acks). get_run_flags returns
    ``top_flags`` on the pre-work check and ``loop_flags`` (default: the same) on the
    in-loop checkpoints, so a control flag flipped mid-stage can be simulated."""
    seen = {"cancelled": 0, "paused": 0, "advanced": 0, "acked": 0}
    calls = {"n": 0}

    def _get_run_flags(_tenant, _run):
        calls["n"] += 1
        if calls["n"] == 1 or loop_flags is None:
            return top_flags
        return loop_flags

    monkeypatch.setattr(queries, "get_run_flags", _get_run_flags)
    monkeypatch.setattr(
        worker,
        "_to_cancelled",
        lambda *a, **k: seen.__setitem__("cancelled", seen["cancelled"] + 1),
    )
    monkeypatch.setattr(
        worker, "_to_paused", lambda *a, **k: seen.__setitem__("paused", seen["paused"] + 1)
    )
    monkeypatch.setattr(worker, "_enter_stage", lambda *a, **k: enter)
    monkeypatch.setattr(worker, "_run_stage_work", stage_work or (lambda *a, **k: None))
    monkeypatch.setattr(progress, "claim_job", lambda *a, **k: claim)
    monkeypatch.setattr(progress, "beat", lambda *a, **k: None)
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: finish)
    monkeypatch.setattr(
        coordinator, "advance", lambda *a, **k: seen.__setitem__("advanced", seen["advanced"] + 1)
    )
    monkeypatch.setattr(
        streams, "ack", lambda *a, **k: seen.__setitem__("acked", seen["acked"] + 1)
    )
    return seen


def _process(stage: str = "discovering") -> str:
    message = {
        "tenant_id": "t",
        "run_id": "r",
        "job_id": "j",
        "stage": stage,
        "attempts": 0,
        "max_attempts": 5,
    }
    return worker.process_message(None, QueueName.DISCOVER, "1-0", message)


def test_process_returns_gone_when_run_vanished(monkeypatch):
    seen = _wire(monkeypatch, top_flags=None)
    assert _process() == "gone"
    assert seen["acked"] == 1


def test_process_skips_a_terminal_run(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags("done"))
    assert _process() == "skipped"
    assert seen["acked"] == 1


def test_process_drops_a_paused_delivery(monkeypatch):
    _wire(monkeypatch, top_flags=_flags("paused"))
    assert _process() == "paused"


def test_process_cancels_on_cancel_request_without_advancing(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags(cancel=True))
    assert _process() == "cancelled"
    assert seen["cancelled"] == 1
    assert seen["advanced"] == 0


def test_process_pauses_on_pause_request(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags(pause=True))
    assert _process() == "paused"
    assert seen["paused"] == 1


def test_process_drops_duplicate_when_claim_lost(monkeypatch):
    _wire(monkeypatch, top_flags=_flags(), claim=False)
    assert _process() == "duplicate"


def test_process_skips_when_stage_entry_is_illegal(monkeypatch):
    _wire(monkeypatch, top_flags=_flags(), enter=False)
    assert _process() == "skipped"


def test_process_happy_path_finishes_and_advances(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags())
    assert _process() == "done"
    assert seen["advanced"] == 1
    assert seen["acked"] == 1


def test_process_does_not_advance_when_finish_claim_lost(monkeypatch):
    # A duplicate that raced this far loses finish_job -> must not double-advance.
    seen = _wire(monkeypatch, top_flags=_flags(), finish=False)
    assert _process() == "done"
    assert seen["advanced"] == 0


def test_process_cancels_mid_loop(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags(), loop_flags=_flags(cancel=True))
    assert _process() == "cancelled"
    assert seen["cancelled"] == 1
    assert seen["advanced"] == 0


def test_process_pauses_mid_loop(monkeypatch):
    seen = _wire(monkeypatch, top_flags=_flags(), loop_flags=_flags(pause=True))
    assert _process() == "paused"
    assert seen["paused"] == 1


def test_process_control_interrupt_cancel_does_not_advance(monkeypatch):
    def _raise(*a, **k):
        raise retry.ControlInterrupt("cancel")

    seen = _wire(monkeypatch, top_flags=_flags(), stage_work=_raise)
    assert _process() == "cancel"
    assert seen["cancelled"] == 1
    assert seen["advanced"] == 0


def test_process_control_interrupt_pause_does_not_advance(monkeypatch):
    def _raise(*a, **k):
        raise retry.ControlInterrupt("pause")

    seen = _wire(monkeypatch, top_flags=_flags(), stage_work=_raise)
    assert _process() == "pause"
    assert seen["paused"] == 1
    assert seen["advanced"] == 0


def test_process_routes_unexpected_error_to_failure_handler(monkeypatch):
    # A non-control error from stage work falls into failure routing (retry/DLQ),
    # not the control-interrupt path. _handle_failure is exercised separately below.
    def _boom(*a, **k):
        raise RuntimeError("stage exploded")

    _wire(monkeypatch, top_flags=_flags(), stage_work=_boom)
    monkeypatch.setattr(worker, "_handle_failure", lambda *a, **k: "retry")
    assert _process() == "retry"


def test_handle_failure_dead_letters_when_retries_exhausted(monkeypatch):
    # Exhausted attempts (attempt_no 6 > max 5) -> DLQ + FAILED, not another retry.
    seen = {"dlq": 0, "to_state": None}
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: True)
    monkeypatch.setattr(streams, "ack", lambda *a, **k: None)
    monkeypatch.setattr(streams, "to_dlq", lambda *a, **k: seen.__setitem__("dlq", seen["dlq"] + 1))
    monkeypatch.setattr(
        worker.service,
        "transition",
        lambda *a, **k: seen.__setitem__("to_state", k.get("to_state")),
    )
    result = worker._handle_failure(
        None,
        QueueName.FETCH,
        "1-0",
        {"attempts": 5, "max_attempts": 5},
        RuntimeError("boom"),
        tenant_id="t",
        run_id="r",
        job_id="j",
        stage=RunStage.FETCHING,
        attempts=5,
        max_attempts=5,
    )
    assert result == "dead"
    assert seen["dlq"] == 1
    assert seen["to_state"] == RunState.FAILED


def test_handle_failure_persists_classified_reason(monkeypatch):
    # The dead path classifies the exception and records the safe subset to
    # run.error + the SSE event; the raw message stays only in error["message"].
    seen: dict = {}
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: True)
    monkeypatch.setattr(streams, "ack", lambda *a, **k: None)
    monkeypatch.setattr(streams, "to_dlq", lambda *a, **k: None)
    monkeypatch.setattr(worker.service, "transition", lambda *a, **k: seen.update(k))
    result = worker._handle_failure(
        None,
        QueueName.FETCH,
        "1-0",
        {"attempts": 5, "max_attempts": 5},
        retry.FatalError("target returned HTTP 403"),
        tenant_id="t",
        run_id="r",
        job_id="j",
        stage=RunStage.FETCHING,
        attempts=5,
        max_attempts=5,
    )
    assert result == "dead"
    err = seen["extra_values"]["error"]
    assert err["category"] == "access_denied"
    assert err["http_status"] == 403
    assert err["message"] == "target returned HTTP 403"  # raw kept for logs only
    ev = seen["event_payload_extra"]
    assert ev["category"] == "access_denied"
    assert "capture extension" in ev["reason"].lower()


def test_handle_failure_swallows_transition_conflict(monkeypatch):
    # A concurrent cancel / reclaimed dead message may have already moved the run out
    # of its active state; the guarded FAILED transition then raises. _handle_failure
    # must NOT propagate (it still ACKs + returns "dead") or the message redelivers.
    acked = {"n": 0}
    monkeypatch.setattr(progress, "finish_job", lambda *a, **k: True)
    monkeypatch.setattr(streams, "ack", lambda *a, **k: acked.__setitem__("n", acked["n"] + 1))
    monkeypatch.setattr(streams, "to_dlq", lambda *a, **k: None)

    def boom(*a, **k):
        raise worker.service.TransitionConflict("run already terminal")

    monkeypatch.setattr(worker.service, "transition", boom)
    result = worker._handle_failure(
        None,
        QueueName.FETCH,
        "1-0",
        {"attempts": 5, "max_attempts": 5},
        RuntimeError("boom"),
        tenant_id="t",
        run_id="r",
        job_id="j",
        stage=RunStage.FETCHING,
        attempts=5,
        max_attempts=5,
    )
    assert result == "dead"
    assert acked["n"] == 1

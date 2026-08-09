"""Hermetic tests for the status ETag (REQ-R4).

`_etag` is the pure kernel of `get_status`'s strong validator. The invariant that
matters: it folds in the cooperative-control flags, so a pause/cancel *request* that
doesn't move `state` still changes the ETag — otherwise an `If-None-Match` poll would
return 304 and the UI's control gating would go stale after a reload.
"""

from __future__ import annotations

import pytest

from recon.runs import queries


def _base() -> dict:
    return {
        "state": "fetching",
        "stage": "fetching",
        "done": 2,
        "total": 4,
        "hb_iso": "2026-01-01T00:00:00+00:00",
        "stalled": False,
        "pause_requested": False,
        "cancel_requested": False,
    }


def test_etag_is_deterministic_and_16_hex():
    etag = queries._etag(**_base())
    assert etag == queries._etag(**_base())  # same inputs -> same validator
    assert len(etag) == 16
    assert all(c in "0123456789abcdef" for c in etag)


def test_etag_pins_the_exact_wire_format():
    # Golden value locks the serialization (field order, ':' delimiter, sha256[:16]).
    # A reorder / delimiter / truncation change would silently break clients' cached
    # If-None-Match validators while every "field participates" assertion still passed.
    assert queries._etag(**_base()) == "419b8154fd198d85"


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("state", "paused"),
        ("stage", "analyzing"),
        ("done", 3),
        ("total", 5),
        ("hb_iso", None),
        ("stalled", True),
        ("pause_requested", True),
        ("cancel_requested", True),
    ],
)
def test_etag_changes_when_any_observed_field_changes(field, new_value):
    base = _base()
    changed = {**base, field: new_value}
    assert queries._etag(**base) != queries._etag(**changed)


def test_pause_request_changes_etag_without_a_state_move():
    # REQ-R4: a cooperative pause flips pause_requested while state stays active
    # until the worker next checkpoints; the validator must reflect that change so
    # a polling client isn't told "304 unchanged" while control gating shifted.
    active = _base()
    pause_pending = {**active, "pause_requested": True}
    assert active["state"] == pause_pending["state"]  # state genuinely unchanged
    assert queries._etag(**active) != queries._etag(**pause_pending)

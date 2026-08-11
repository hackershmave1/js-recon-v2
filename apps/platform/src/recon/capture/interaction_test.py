"""Host-lane unit tests for the interaction orchestration (autoscroll / click-all /
route-enum decision logic), driving a fake ``_Ctx`` so no browser is needed. The pump
+ eager-fetch wiring is exercised through the real driver in ``driver_test`` and against
real Chromium in ``driver_integration_test``."""

from __future__ import annotations

from recon.capture import interaction
from recon.capture.interaction import InteractConfig


def _cfg(**over) -> InteractConfig:
    base = {
        "enabled": True,
        "max_scroll_steps": 12,
        "max_clicks": 40,
        "max_routes": 15,
        "idle_settle_s": 0.01,
        "nav_timeout_s": 0.1,
    }
    base.update(over)
    return InteractConfig(**base)


class _FakeCtx:
    """Records the calls the interaction driver makes and answers ``evaluate`` from a
    per-expression script. ``stop_after`` makes ``past_deadline`` trip after N evals."""

    def __init__(self, *, answers, target_url="https://acme.io/", stop_after=None, cap_after=None):
        self.target_url = target_url
        self._answers = answers  # expression -> RemoteObject dict (or None)
        self._stop_after = stop_after
        self._cap_after = cap_after
        self.evals: list[str] = []
        self.navigations: list[str] = []
        self.settles = 0

    def evaluate(self, expression, *, timeout_s):
        self.evals.append(expression)
        answer = self._answers.get(expression, {})
        return answer(len(self.evals)) if callable(answer) else answer

    def navigate_page(self, url, *, timeout_s):
        self.navigations.append(url)

    def settle(self, *, budget_s):
        self.settles += 1

    def past_deadline(self):
        return self._stop_after is not None and len(self.evals) >= self._stop_after

    def at_cap(self):
        return self._cap_after is not None and len(self.evals) >= self._cap_after


def test_autoscroll_steps_to_the_bottom_then_stops():
    # [scrollY, innerHeight, scrollHeight]: two steps still short of the bottom, then a
    # third that reaches the bottom of a page that has stopped growing -> stop.
    steps = iter([[800, 800, 3000], [1600, 800, 3000], [2400, 800, 3000]])
    ctx = _FakeCtx(answers={interaction._SCROLL_JS: lambda _n: {"value": next(steps)}})
    interaction._autoscroll(ctx, _cfg(max_scroll_steps=5), settle_budget=0.1)
    assert ctx.evals == [interaction._SCROLL_JS] * 3
    assert ctx.settles == 3  # a settle after every scroll step


def test_autoscroll_stops_at_the_step_cap():
    # An infinite-scroll page keeps growing scrollHeight, so it never reaches a stable
    # bottom -> bounded only by max_scroll_steps.
    ctx = _FakeCtx(answers={interaction._SCROLL_JS: lambda n: {"value": [n * 800, 800, n * 5000]}})
    interaction._autoscroll(ctx, _cfg(max_scroll_steps=4), settle_budget=0.1)
    assert len(ctx.evals) == 4  # bounded by max_scroll_steps


def test_autoscroll_stops_on_eval_failure():
    ctx = _FakeCtx(answers={interaction._SCROLL_JS: None})  # eval failed / context gone
    interaction._autoscroll(ctx, _cfg(), settle_budget=0.1)
    assert len(ctx.evals) == 1


def test_click_all_clicks_each_in_order_until_navigation():
    answers = {
        interaction._SNAPSHOT_JS: {"value": 3},
        interaction._click_js(0): {"value": "ok"},
        interaction._click_js(1): {"value": "navigated"},  # ends the batch
        interaction._click_js(2): {"value": "ok"},
    }
    ctx = _FakeCtx(answers=answers)
    interaction._click_all(ctx, _cfg(), settle_budget=0.1)
    assert ctx.evals == [
        interaction._SNAPSHOT_JS,
        interaction._click_js(0),
        interaction._click_js(1),
    ]


def test_click_all_respects_the_click_cap():
    answers = {interaction._SNAPSHOT_JS: {"value": 10}}
    for i in range(10):
        answers[interaction._click_js(i)] = {"value": "ok"}
    ctx = _FakeCtx(answers=answers)
    interaction._click_all(ctx, _cfg(max_clicks=2), settle_budget=0.1)
    assert ctx.evals == [
        interaction._SNAPSHOT_JS,
        interaction._click_js(0),
        interaction._click_js(1),
    ]


def test_click_all_stops_when_context_is_gone():
    answers = {interaction._SNAPSHOT_JS: {"value": 3}, interaction._click_js(0): None}
    ctx = _FakeCtx(answers=answers)
    interaction._click_all(ctx, _cfg(), settle_budget=0.1)
    assert ctx.evals == [interaction._SNAPSHOT_JS, interaction._click_js(0)]


def test_click_all_no_interactive_elements():
    ctx = _FakeCtx(answers={interaction._SNAPSHOT_JS: {"value": 0}})
    interaction._click_all(ctx, _cfg(), settle_budget=0.1)
    assert ctx.evals == [interaction._SNAPSHOT_JS]  # snapshot only, no clicks


def test_collect_routes_dedupes_excludes_seed_and_caps():
    urls = [
        "https://acme.io/a",
        "https://acme.io/a",  # duplicate -> collapsed
        "https://acme.io/",  # the seed target_url -> excluded
        "https://acme.io/b",
        "https://acme.io/c",
    ]
    ctx = _FakeCtx(answers={interaction._ROUTES_JS: {"value": urls}})
    routes = interaction._collect_routes(ctx, _cfg(max_routes=2))
    assert routes == [
        "https://acme.io/a",
        "https://acme.io/b",
    ]  # deduped, seed-excluded, capped at 2


def test_collect_routes_non_list_result_is_empty():
    ctx = _FakeCtx(answers={interaction._ROUTES_JS: {"value": "not-a-list"}})
    assert interaction._collect_routes(ctx, _cfg()) == []


def test_run_drives_seed_then_each_route():
    answers = {
        interaction._SCROLL_JS: None,  # no lazy content
        interaction._SNAPSHOT_JS: {"value": 0},  # no clickables
        interaction._ROUTES_JS: {"value": ["https://acme.io/x", "https://acme.io/y"]},
    }
    ctx = _FakeCtx(answers=answers)
    interaction.run(ctx, _cfg())
    assert ctx.navigations == ["https://acme.io/x", "https://acme.io/y"]
    # seed page + 2 routes each get a scroll + snapshot pass (route-enum re-collects once).
    assert ctx.evals.count(interaction._SCROLL_JS) == 3
    assert ctx.evals.count(interaction._SNAPSHOT_JS) == 3


def test_run_stops_at_deadline_before_navigating_routes():
    answers = {
        interaction._SCROLL_JS: None,
        interaction._SNAPSHOT_JS: {"value": 0},
        interaction._ROUTES_JS: {"value": ["https://acme.io/x"]},
    }
    # Trip the deadline as soon as the seed page + route collection are done, before the
    # route navigation loop acts.
    ctx = _FakeCtx(answers=answers, stop_after=3)
    interaction.run(ctx, _cfg())
    assert ctx.navigations == []  # deadline reached -> no route navigation


def test_collect_routes_drops_off_origin_targets():
    # A click can pivot the page off-origin (e.g. an SSO redirect); route-enum must stay on
    # the SEED origin and not enumerate the off-origin site's links.
    urls = ["https://acme.io/a", "https://evil.example/x", "https://acme.io/b"]
    ctx = _FakeCtx(answers={interaction._ROUTES_JS: {"value": urls}})
    assert interaction._collect_routes(ctx, _cfg()) == ["https://acme.io/a", "https://acme.io/b"]


def test_run_stops_when_script_cap_reached():
    # Once the script cap is hit, interaction aborts remaining actions instead of burning
    # the session budget — no route navigation happens.
    answers = {
        interaction._SCROLL_JS: {"value": 100},
        interaction._SNAPSHOT_JS: {"value": 0},
        interaction._ROUTES_JS: {"value": ["https://acme.io/x"]},
    }
    ctx = _FakeCtx(answers=answers, cap_after=1)  # capped after the first eval
    interaction.run(ctx, _cfg())
    assert ctx.navigations == []

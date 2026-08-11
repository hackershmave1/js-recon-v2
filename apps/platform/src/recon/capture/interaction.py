"""Interaction driver for runtime capture (slice 3).

Once the initial page load settles, drive the app like a user so lazily-loaded /
route-split / click-gated JS actually executes and gets captured by the driver's
``scriptParsed`` pump:

1. **Autoscroll to idle** — scroll to the bottom in bounded steps until the document
   stops growing, triggering ``IntersectionObserver`` / lazy-import / infinite-scroll
   chunks.
2. **Click-all** — snapshot every interactive element (buttons, links, ``[role=button]``,
   submits, ``summary`` …) in DOM order and click each, revealing modal / tab / accordion
   chunks. Off-origin anchors are skipped and a click that navigates ends the batch (the
   DOM snapshot is then stale; route-enum covers systematic navigation).
3. **Route-enum** — collect same-origin ``<a href>`` targets and navigate each, then
   autoscroll + click that page too, so per-route split bundles load.

Everything runs through ``_Ctx`` (see ``recon.capture.driver``): each action issues a
``Runtime.evaluate`` / ``Page.navigate`` and then pumps until the page re-settles, so
new parses are captured (and their sources fetched eagerly, before the next navigation
destroys the context). Every action is bounded and every wait beats the job lease and
observes pause/cancel; nothing extends the global session deadline.

Scope: route-enum is SAME-ORIGIN only and click-all does not follow off-origin links —
this keeps capture on the target being recon'd (and is the cheap half of the egress
posture; request-layer egress enforcement is the deferred egress-proxy work). The stage
still re-validates every captured script's URL against scope before storing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:  # avoid an import cycle — driver imports this module
    from recon.capture.driver import _Ctx


@dataclass(frozen=True)
class InteractConfig:
    enabled: bool
    max_scroll_steps: int
    max_clicks: int
    max_routes: int
    idle_settle_s: float
    nav_timeout_s: float


# Step down by one viewport and report [scrollY, innerHeight, scrollHeight]; autoscroll
# steps until it reaches the bottom of a page that has stopped growing. Stepping (not one
# jump to the bottom) is what lets a mid-page IntersectionObserver sentinel fire.
_SCROLL_JS = (
    "(()=>{window.scrollBy(0,window.innerHeight);"
    "return [window.scrollY,window.innerHeight,document.body.scrollHeight];})()"
)

# Tag every interactive element with a stable index in DOM order and return the count,
# so click-all can address each by index even as the DOM mutates between clicks.
_SNAPSHOT_JS = (
    "(()=>{"
    "const q='button,[role=button],a[href],[onclick],input[type=submit],"
    "input[type=button],summary,[tabindex]';"
    "const els=Array.from(document.querySelectorAll(q));"
    "els.forEach((e,i)=>e.setAttribute('data-recon-idx',i));"
    "return els.length;})()"
)

# Collect same-origin, http(s), de-hashed link targets in DOM order.
_ROUTES_JS = (
    "(()=>{const seen=new Set();const out=[];"
    "for(const a of document.querySelectorAll('a[href]')){"
    "let u;try{u=new URL(a.href,location.href);}catch(_){continue;}"
    "if(u.origin!==location.origin)continue;"
    "if(u.protocol!=='http:'&&u.protocol!=='https:')continue;"
    "u.hash='';const s=u.href;"
    "if(!seen.has(s)){seen.add(s);out.push(s);}}"
    "return out;})()"
)


def _click_js(idx: int) -> str:
    # Click element ``idx`` if present and not an off-origin anchor; report whether the
    # click navigated the top document (so the caller can stop — the snapshot is stale).
    return (
        "(()=>{"
        f"const e=document.querySelector('[data-recon-idx=\"{idx}\"]');"
        "if(!e)return 'gone';"
        "if(e.tagName==='A'&&e.href){try{"
        "if(new URL(e.href,location.href).origin!==location.origin)return 'xorigin';"
        "}catch(_){}}"
        "const before=location.href;e.click();"
        "return location.href===before?'ok':'navigated';})()"
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _stop(ctx: _Ctx) -> bool:
    # Stop interaction early if the session deadline passed or the script cap was hit
    # (further parses are dropped, so more actions would only spend budget).
    return ctx.past_deadline() or ctx.at_cap()


def run(ctx: _Ctx, cfg: InteractConfig) -> None:
    """Drive the seed page, then walk same-origin routes, autoscrolling + clicking each.
    All new parses land in ``ctx`` via its pump; sources are fetched eagerly there."""
    settle_budget = cfg.idle_settle_s * 3.0 + 1.0
    _drive_page(ctx, cfg, settle_budget)
    if _stop(ctx):
        return
    for url in _collect_routes(ctx, cfg):
        if _stop(ctx):
            return
        ctx.navigate_page(url, timeout_s=cfg.nav_timeout_s)
        ctx.settle(budget_s=settle_budget)
        _drive_page(ctx, cfg, settle_budget)


def _drive_page(ctx: _Ctx, cfg: InteractConfig, settle_budget: float) -> None:
    _autoscroll(ctx, cfg, settle_budget)
    _click_all(ctx, cfg, settle_budget)


def _autoscroll(ctx: _Ctx, cfg: InteractConfig, settle_budget: float) -> None:
    last_height = -1.0
    for _ in range(cfg.max_scroll_steps):
        if _stop(ctx):
            return
        result = ctx.evaluate(_SCROLL_JS, timeout_s=cfg.nav_timeout_s)
        ctx.settle(budget_s=settle_budget)
        value = result.get("value") if result else None
        if not isinstance(value, list) or len(value) != 3:
            return  # eval failed / unexpected shape — done scrolling
        scroll_y, viewport_h, scroll_height = value
        if not all(isinstance(n, (int, float)) for n in value):
            return
        # Stop once we've reached the bottom AND the page is no longer growing (an
        # infinite-scroll page keeps growing scroll_height, so keep stepping until it settles).
        if scroll_y + viewport_h >= scroll_height - 1 and scroll_height <= last_height:
            return
        last_height = float(scroll_height)


def _click_all(ctx: _Ctx, cfg: InteractConfig, settle_budget: float) -> None:
    if _stop(ctx):
        return
    snapshot = ctx.evaluate(_SNAPSHOT_JS, timeout_s=cfg.nav_timeout_s)
    count = snapshot.get("value") if snapshot else None
    if not isinstance(count, (int, float)):
        return
    for idx in range(min(int(count), cfg.max_clicks)):
        if _stop(ctx):
            return
        result = ctx.evaluate(_click_js(idx), timeout_s=cfg.nav_timeout_s)
        ctx.settle(budget_s=settle_budget)
        if result is None or result.get("value") == "navigated":
            # The click navigated (or destroyed the context) — the index snapshot is
            # stale. Stop; route-enum handles systematic navigation deterministically.
            return


def _collect_routes(ctx: _Ctx, cfg: InteractConfig) -> list[str]:
    result = ctx.evaluate(_ROUTES_JS, timeout_s=cfg.nav_timeout_s)
    urls = result.get("value") if result else None
    if not isinstance(urls, list):
        return []
    seed_origin = _origin(ctx.target_url)
    visited = {ctx.target_url, ctx.target_url.split("#", 1)[0]}
    routes: list[str] = []
    for url in urls:
        if not isinstance(url, str) or url in visited:
            continue
        # Filter against the SEED origin, not the live page: a click may have pivoted the
        # page off-origin (e.g. an onclick SSO redirect), and route-enum must stay on the
        # target being recon'd rather than walking the off-origin site's links.
        if _origin(url) != seed_origin:
            continue
        visited.add(url)
        routes.append(url)
        if len(routes) >= cfg.max_routes:
            break
    return routes

"""Third-party analytics / telemetry / vendor hosts filtered from findings BY DEFAULT.

A recon tool's job is the TARGET's own API surface. A hardcoded ``fetch`` to Amplitude,
Google Analytics, Sentry, Stripe, Segment, etc. is vendor boilerplate every SPA ships — it
is noise, not the target's backend, and it drowns the real endpoints/hosts an operator cares
about. So a finding (or host-inventory row) whose host is one of these is hidden by default;
a run/view can opt back in ("include noisy analytics") to see everything.

This is a READ-TIME, REVERSIBLE overlay (like the base-URL rules): the finding is still
written and stored — nothing is deleted — it is only dropped from the default read model, so
toggling the override back on surfaces it again with no re-analysis. The match is an EXACT
host or dot-suffix test (``h == d or h.endswith("." + d)``), the same rule ``egress`` uses for
scope, so ``notamplitude.com`` never false-matches ``amplitude.com``.

The list is deliberately conservative-to-broad on well-known analytics/telemetry/ad/CDN
vendors; a host the target genuinely owns (its own API) is never on it. Extend as real
dogfooding surfaces new noise — this is the single source of truth (mirrors the capture
extension's own default profile, unified app-wide, DEBT/QA #3).
"""

from __future__ import annotations

# Exact host or dot-suffix match. Grouped by vendor class for maintenance; the runtime set is
# the flat union below.
_ANALYTICS = {
    "google-analytics.com",
    "googletagmanager.com",
    "amplitude.com",
    "mixpanel.com",
    "segment.com",
    "segment.io",
    "heapanalytics.com",
    "hotjar.com",
    "fullstory.com",
    "mouseflow.com",
    "quantserve.com",
    "scorecardresearch.com",
    "tealiumiq.com",
    "tiqcdn.com",
}
_TELEMETRY = {
    "sentry.io",
    "sentry-cdn.com",
    "ingest.sentry.io",
    "bugsnag.com",
    "datadoghq.com",
    "datadoghq-browser-agent.com",
    "newrelic.com",
    "nr-data.net",
    "cloudflareinsights.com",
    "logrocket.com",
    "logrocket.io",
    "testfairy.com",
}
_ADS_MARKETING = {
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "adservice.google.com",
    "braze.com",
    "appboycdn.com",
    "branch.io",
    "addthis.com",
    "optimizely.com",
    "launchdarkly.com",
    "intercom.io",
    "intercomcdn.com",
    "intercomassets.com",
    "drift.com",
    "zdassets.com",
}
_VENDOR_CDN = {
    "gstatic.com",
    "googleapis.com",
    "gvt1.com",
    "gvt2.com",
    "google.com",
    "stripe.com",
    "stripe.network",
    "js.stripe.com",
    "recaptcha.net",
    "facebook.com",
    "facebook.net",
    "fbcdn.net",
    "connect.facebook.net",
    "apple.com",
    "apple.news",
    "mozilla.org",
    "wappalyzer.com",
}

# The flat runtime denylist (single source of truth).
DEFAULT_NOISE_HOSTS: frozenset[str] = frozenset(
    _ANALYTICS | _TELEMETRY | _ADS_MARKETING | _VENDOR_CDN
)


def is_noise_host(host: str | None, *, denylist: frozenset[str] = DEFAULT_NOISE_HOSTS) -> bool:
    """Whether ``host`` is a known third-party analytics/telemetry/vendor host — an EXACT
    match or a dot-suffix of a denylisted domain. A host-less finding (``None``) is never
    noise (a relative endpoint is the target's own surface, kept)."""
    if not host:
        return False
    normalized = host.strip().lower().rstrip(".")
    return any(normalized == entry or normalized.endswith("." + entry) for entry in denylist)


def is_all_noise(hosts: set[str | None]) -> bool:
    """Whether a finding should be hidden: it HAS at least one attributed host and EVERY
    attributed host is noise. A finding with no host (relative path), or seen on even one
    non-noise host (e.g. an analytics call that ALSO hits the target), is kept — the filter
    never drops a finding that carries real in-scope surface."""
    attributed = {h for h in hosts if h}
    return bool(attributed) and all(is_noise_host(h) for h in attributed)

"""Prometheus metrics definitions (D53-a, REQ-S3).

Metric objects are module-level singletons; prometheus_client handles multiprocess
file I/O automatically when PROMETHEUS_MULTIPROC_DIR is set in the environment.
Both the api and worker processes import this module and write to the shared dir;
the /metrics endpoint aggregates via MultiProcessCollector.

In tests (no PROMETHEUS_MULTIPROC_DIR) the module operates in single-process mode
and the same Counter/Histogram/Gauge objects are used directly.
"""

from __future__ import annotations

import os

from prometheus_client import Counter, Histogram

# Create the multiprocess dir eagerly so prometheus-client can open its mmap
# files immediately on the first .inc()/.observe(). A tmpfs or anonymous Docker
# volume guarantees the dir is clean on boot (no stale PID files from prior runs);
# the makedirs here covers any environment where the dir might not yet exist.
if _prom_dir := os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
    os.makedirs(_prom_dir, exist_ok=True)

# ── worker metrics ────────────────────────────────────────────────────────────

jobs_total: Counter = Counter(
    "recon_jobs_total",
    "Jobs processed by the worker, by queue and outcome",
    ["queue", "outcome"],
)

job_duration_seconds: Histogram = Histogram(
    "recon_job_duration_seconds",
    "End-to-end worker job duration in seconds",
    ["queue"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

# ── api metrics ───────────────────────────────────────────────────────────────

http_requests_total: Counter = Counter(
    "recon_http_requests_total",
    "HTTP requests handled by the API, by method, path, and status",
    ["method", "path", "status"],
)


def make_scrape_registry():
    """Return a registry that aggregates all process metric files.

    When PROMETHEUS_MULTIPROC_DIR is set, MultiProcessCollector reads every
    .db file written by the api and worker processes and sums them. Without
    it (e.g. in tests), the default REGISTRY is returned directly.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import CollectorRegistry
        from prometheus_client.multiprocess import MultiProcessCollector

        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        return registry

    from prometheus_client import REGISTRY

    return REGISTRY

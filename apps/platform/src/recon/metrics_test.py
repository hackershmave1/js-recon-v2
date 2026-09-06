"""Tests for recon.metrics (D53-a)."""

from __future__ import annotations

from recon.metrics import (
    http_requests_total,
    job_duration_seconds,
    jobs_total,
    make_scrape_registry,
)


def test_jobs_total_labels_increment():
    before = jobs_total.labels(queue="discover", outcome="done")._value.get()
    jobs_total.labels(queue="discover", outcome="done").inc()
    after = jobs_total.labels(queue="discover", outcome="done")._value.get()
    assert after == before + 1.0


def test_job_duration_records_observation():
    before = job_duration_seconds.labels(queue="fetch")._sum.get()
    job_duration_seconds.labels(queue="fetch").observe(5.0)
    after = job_duration_seconds.labels(queue="fetch")._sum.get()
    assert after == before + 5.0


def test_http_requests_total_increments():
    before = http_requests_total.labels(method="GET", path="/healthz", status="200")._value.get()
    http_requests_total.labels(method="GET", path="/healthz", status="200").inc()
    after = http_requests_total.labels(method="GET", path="/healthz", status="200")._value.get()
    assert after == before + 1.0


def test_make_scrape_registry_no_multiproc_env(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    from prometheus_client import REGISTRY

    assert make_scrape_registry() is REGISTRY

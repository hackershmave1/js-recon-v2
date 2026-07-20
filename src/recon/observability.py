"""Structured logging with run_id correlation (REQ-S3).

A single ``run_id`` (and optional ``stage``) is bound to a context variable so
every log line emitted while handling a request or a job carries the correlation
id automatically — the same id flows API -> queue -> worker.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import structlog

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_stage: ContextVar[str | None] = ContextVar("stage", default=None)

_configured = False


def _correlation_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    run_id = _run_id.get()
    stage = _stage.get()
    if run_id is not None:
        event_dict.setdefault("run_id", run_id)
    if stage is not None:
        event_dict.setdefault("stage", stage)
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Idempotently configure structlog for the process."""
    global _configured
    if _configured:
        return
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _correlation_processor,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


@contextmanager
def bind_run(run_id: str, stage: str | None = None) -> Iterator[None]:
    """Bind run_id/stage for the duration of a request or job handler."""
    run_token = _run_id.set(run_id)
    stage_token = _stage.set(stage) if stage is not None else None
    try:
        yield
    finally:
        _run_id.reset(run_token)
        if stage_token is not None:
            _stage.reset(stage_token)


def set_stage(stage: str | None) -> None:
    _stage.set(stage)

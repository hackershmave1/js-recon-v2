from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, TypeVar


T = TypeVar("T")


def run_coroutine_sync(awaitable: Awaitable[T], timeout: float | None = None) -> T:
    """
    Run an async coroutine from synchronous code.

    If no event loop is running in this thread, execute directly with asyncio.run.
    If a loop is already running (for example, save_files called from async recon flow),
    execute the coroutine in a dedicated background thread.
    """

    def _execute() -> T:
        if timeout is not None:
            return asyncio.run(asyncio.wait_for(awaitable, timeout=timeout))
        return asyncio.run(awaitable)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _execute()

    result: dict[str, T] = {}
    error: dict[str, BaseException] = {}
    done = threading.Event()

    def _thread_target() -> None:
        try:
            result["value"] = _execute()
        except BaseException as exc:  # pragma: no cover - passthrough
            error["exc"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()
    done.wait()

    if "exc" in error:
        raise error["exc"]
    return result["value"]

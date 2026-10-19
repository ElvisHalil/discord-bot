"""
Centralized Context-Aware Background Worker Pool.
Offloads blocking I/O, web scraping, and API aggregation from the main asyncio loop
while preserving per-turn ContextVars (such as ACTIVE_CHANNEL_ID and ACTIVE_MEMORY_FILE)
across worker and sub-worker threads.
"""

import asyncio
import concurrent.futures.thread as _cft
import contextvars
import threading
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterable, List, Optional, TypeVar

T = TypeVar("T")


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that spawns daemon=True worker threads so CPython 3.14's
    C-level _thread_shutdown() never hangs on in-flight background scrapes at Ctrl+C exit."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
            if hasattr(self, "_create_worker_context"):
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._create_worker_context(),
                    self._work_queue,
                )
            else:
                worker_args = (
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                )
            t = threading.Thread(
                name=thread_name,
                target=_cft._worker,
                args=worker_args,
                daemon=True,
            )
            t.start()
            self._threads.add(t)


_orig_threading_shutdown = getattr(threading, "_shutdown", None)
if callable(_orig_threading_shutdown) and not getattr(_orig_threading_shutdown, "_bot_wrapped", False):
    def _quiet_threading_shutdown() -> None:
        try:
            _orig_threading_shutdown()
        except KeyboardInterrupt:
            pass

    _quiet_threading_shutdown._bot_wrapped = True  # type: ignore[attr-defined]
    threading._shutdown = _quiet_threading_shutdown  # type: ignore[attr-defined]


# Top-level executor for agent tool turns (`run_in_worker`)
WORKER_POOL = _DaemonThreadPoolExecutor(max_workers=16, thread_name_prefix="BotTurnWorker")

# Sub-worker executor for synchronous fan-out inside skills (`submit_to_worker`, `map_in_workers`)
# Separated from WORKER_POOL so parent tool threads waiting on child futures can never starve or deadlock.
SUB_WORKER_POOL = _DaemonThreadPoolExecutor(max_workers=24, thread_name_prefix="BotSubWorker")


async def run_in_worker(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run a synchronous/blocking callable in WORKER_POOL without blocking the asyncio event loop,
    preserving the caller's contextvars.Context in its own isolated copy.
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(WORKER_POOL, lambda: ctx.run(func, *args, **kwargs))


def submit_to_worker(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
    """
    Submit a callable to SUB_WORKER_POOL from synchronous code, giving the worker task
    its own isolated copy of the caller's contextvars.Context.
    """
    ctx = contextvars.copy_context()
    return SUB_WORKER_POOL.submit(ctx.run, func, *args, **kwargs)


def map_in_workers(
    func: Callable[[Any], T],
    iterable: Iterable[Any],
    timeout: Optional[float] = None,
) -> List[T]:
    """
    Map a callable across an iterable concurrently in SUB_WORKER_POOL, giving each item
    its own isolated copy of the caller's contextvars.Context.
    """
    items = list(iterable)
    if not items:
        return []
    if len(items) == 1 or (
        threading.current_thread().name.startswith("BotSubWorker")
        and len(SUB_WORKER_POOL._threads) >= SUB_WORKER_POOL._max_workers - 4
    ):
        return [func(item) for item in items]
    futures = [submit_to_worker(func, item) for item in items]
    return [fut.result(timeout=timeout) for fut in futures]


def shutdown_worker_pool() -> None:
    """Cleanly shut down the shared worker pools and signal any stdlib executor queues on exit."""
    for pool in (WORKER_POOL, SUB_WORKER_POOL):
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    try:
        for q in list(_cft._threads_queues.values()):
            try:
                q.put(None)
            except Exception:
                pass
        _cft._threads_queues.clear()
    except Exception:
        pass


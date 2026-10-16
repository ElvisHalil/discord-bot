"""
Centralized Context-Aware Background Worker Pool.
Offloads blocking I/O, web scraping, and API aggregation from the main asyncio loop
while preserving per-turn ContextVars (such as ACTIVE_CHANNEL_ID and ACTIVE_MEMORY_FILE)
across worker and sub-worker threads.
"""

import asyncio
import concurrent.futures.thread as _cft
import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterable, List, Optional, TypeVar

T = TypeVar("T")

# Top-level executor for agent tool turns (`run_in_worker`)
WORKER_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="BotTurnWorker")

# Sub-worker executor for synchronous fan-out inside skills (`submit_to_worker`, `map_in_workers`)
# Separated from WORKER_POOL so parent tool threads waiting on child futures can never starve or deadlock.
SUB_WORKER_POOL = ThreadPoolExecutor(max_workers=24, thread_name_prefix="BotSubWorker")


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
    if len(items) == 1:
        return [func(items[0])]
    futures = [submit_to_worker(func, item) for item in items]
    return [fut.result(timeout=timeout) for fut in futures]


def shutdown_worker_pool() -> None:
    """Cleanly shut down the shared worker pools on process exit without blocking Ctrl+C."""
    for pool in (WORKER_POOL, SUB_WORKER_POOL):
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    try:
        _cft._threads_queues.clear()
    except Exception:
        pass


"""Tool wrapper: timing + structured logging + exception → user-facing string."""
from __future__ import annotations

import contextvars
import functools
import inspect
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

import structlog

_logger = structlog.get_logger("rpm-mcp.server")

# Per-task scratch state. _log_extras feeds the wrapper's terminal log record;
# _stale_state is set by helpers that fell back to cached data so the wrapper
# can prepend a one-line WARNING banner.
_log_extras: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_log_extras", default={}
)
_stale_state: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "_stale_state", default=None
)


def _tlog(**fields: Any) -> None:
    """Attach structured fields to the wrapping tool's terminal log record."""
    _log_extras.set({**_log_extras.get(), **fields})


def _mark_stale(synced_at: datetime | None) -> None:
    """Flag the current tool call as serving stale data."""
    _stale_state.set(synced_at)


def _stale_banner(synced_at: datetime | None) -> str:
    ts = synced_at.isoformat() if synced_at else "unknown timestamp"
    return f"WARNING: source fetch failed; serving cached data from {ts}\n\n"


def _tool_wrapper(
    tool_name: str,
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Wrap a tool body with timing, structured logging, and uniform errors."""

    def decorator(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            extras_token = _log_extras.set({})
            stale_token = _stale_state.set(None)
            t0 = time.perf_counter()
            bound: Mapping[str, Any]
            try:
                bound = sig.bind(*args, **kwargs).arguments
            except TypeError:
                bound = kwargs
            log = _logger.bind(
                tool=tool_name,
                **{k: v for k, v in bound.items() if isinstance(v, (str, int, bool))},
            )
            try:
                result = await fn(*args, **kwargs)
                stale_at = _stale_state.get()
                log.info(
                    "tool_done",
                    elapsed_s=round(time.perf_counter() - t0, 3),
                    stale=stale_at is not None,
                    **_log_extras.get(),
                )
                return _stale_banner(stale_at) + result if stale_at is not None else result
            except Exception as e:
                log.exception(
                    "tool_error",
                    elapsed_s=round(time.perf_counter() - t0, 3),
                    **_log_extras.get(),
                )
                return f"Error in {tool_name} for {bound.get('package', '?')}: {e}"
            finally:
                _log_extras.reset(extras_token)
                _stale_state.reset(stale_token)

        return wrapper

    return decorator

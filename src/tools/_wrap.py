"""Tool wrapper: timing + structured logging + typed exception dispatch."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

import asyncpg
import structlog

from ..errors import DBError, ValidationError
from ..sources.base import SourceError, SourceNotFound

_logger = structlog.get_logger("rpm-mcp.server")

# Per-task scratch state. _log_extras feeds the wrapper's terminal log record;
# _stale_state is set by helpers that fell back to cached data so the wrapper
# can prepend a one-line WARNING banner.
_log_extras: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_log_extras", default=None
)
_stale_state: contextvars.ContextVar[datetime | None] = contextvars.ContextVar("_stale_state", default=None)
# When True, the wrapper suppresses the <rpm-mcp:untrusted-data> envelope.
# Set by run_cli before invoking a tool one-shot from the shell, so humans
# reading raw CLI output don't see XML tags wrapping the body.
_suppress_envelope: contextvars.ContextVar[bool] = contextvars.ContextVar("_suppress_envelope", default=False)


def suppress_untrusted_envelope() -> None:
    """Call from CLI entrypoints to disable the S7b output envelope."""
    _suppress_envelope.set(True)


def _tlog(**fields: Any) -> None:
    """Attach structured fields to the wrapping tool's terminal log record."""
    _log_extras.set({**(_log_extras.get() or {}), **fields})


def _mark_stale(synced_at: datetime | None) -> None:
    """Flag the current tool call as serving stale data."""
    _stale_state.set(synced_at)


def _stale_banner(synced_at: datetime | None) -> str:
    ts = synced_at.isoformat() if synced_at else "unknown timestamp"
    return f"WARNING: source fetch failed; serving cached data from {ts}\n\n"


def _wrap_untrusted(body: str, sources: tuple[str, ...]) -> str:
    """S7b: envelope tool output so the consuming LLM treats it as data, not
    instructions. Skipped on CLI to keep human-facing output clean.
    """
    if not sources or _suppress_envelope.get():
        return body
    src = ",".join(sources)
    return f'<rpm-mcp:untrusted-data sources="{src}">\n{body}\n</rpm-mcp:untrusted-data>'


def _resolve_timeout(category: str | None) -> float | None:
    """Return timeout in seconds for *category*, or None for no limit."""
    from ..config import settings

    if category == "fast":
        return float(settings.tool_timeout_fast_s)
    if category == "search":
        return float(settings.tool_timeout_search_s)
    return None  # sync/ingest tools: no cap


def _tool_wrapper(
    tool_name: str,
    untrusted_sources: tuple[str, ...] = (),
    category: str | None = None,
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Wrap a tool body with timing, typed error dispatch, and structured logging.

    *category* controls the asyncio timeout:
      ``'fast'``   -- short DB-read tools (TOOL_TIMEOUT_FAST_S, default 10s)
      ``'search'`` -- vector/FTS/live-API tools (TOOL_TIMEOUT_SEARCH_S, default 30s)
      ``None``     -- sync/ingest tools: no timeout applied

    ``untrusted_sources`` lists external data origins for the S7b envelope.
    """
    timeout = _resolve_timeout(category)
    _category_label = category or "none"

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
                coro = fn(*args, **kwargs)
                result = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
                stale_at = _stale_state.get()
                elapsed = time.perf_counter() - t0
                log.info(
                    "tool_done",
                    elapsed_s=round(elapsed, 3),
                    duration_ms=round(elapsed * 1000),
                    category=_category_label,
                    stale=stale_at is not None,
                    **(_log_extras.get() or {}),
                )
                body = _wrap_untrusted(result, untrusted_sources)
                return _stale_banner(stale_at) + body if stale_at is not None else body
            except TimeoutError:
                elapsed = round(time.perf_counter() - t0, 3)
                log.warning("tool_timeout", elapsed_s=elapsed, timeout_s=timeout)
                return (
                    f"Tool '{tool_name}' exceeded the {timeout:.0f}s time limit. "
                    "Try a more specific query, or use the worker for large operations."
                )
            except ValidationError as e:
                log.warning(
                    "tool_validation_error", error=str(e), elapsed_s=round(time.perf_counter() - t0, 3)
                )
                return f"Invalid input: {e}"
            except SourceNotFound:
                log.info("tool_source_not_found", elapsed_s=round(time.perf_counter() - t0, 3))
                return "Package not found in any configured source. Try sync_package to ingest it first."
            except SourceError as e:
                # Why: log the raw error for ops but don't echo it back -- source
                # errors can include internal URLs and upstream status payloads.
                log.warning("tool_source_error", error=str(e), elapsed_s=round(time.perf_counter() - t0, 3))
                return "Data source temporarily unavailable. Try again later or check your network connection."
            except (DBError, asyncpg.PostgresError) as e:
                # DB error messages can include SQL fragments, schema names, and
                # connection metadata -- log them but keep the reply opaque.
                log.error("tool_db_error", error=str(e), elapsed_s=round(time.perf_counter() - t0, 3))
                return "Database error -- ingestion is degraded. Check the server logs."
            except Exception:
                # Catch-all: log full traceback + extras, but reply with a stable
                # generic message that carries no user input or exception state.
                log.exception(
                    "tool_error",
                    elapsed_s=round(time.perf_counter() - t0, 3),
                    **(_log_extras.get() or {}),
                )
                return f"Unexpected error in {tool_name}. See server logs for details."
            finally:
                _log_extras.reset(extras_token)
                _stale_state.reset(stale_token)

        return wrapper

    return decorator

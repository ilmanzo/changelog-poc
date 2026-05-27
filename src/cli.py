"""Argparse-based CLI: invoke any tool directly from the shell."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from src.runtime import db, source_registry
from src.tools import ALL_CLI_TOOLS
from src.tools._wrap import suppress_untrusted_envelope

_logger = structlog.get_logger("rpm-mcp.cli")
_ParamType = type[str] | type[int] | type[float] | type[bool]
_TYPE_MAP: dict[str, _ParamType] = {"str": str, "int": int, "float": float, "bool": bool}


def _resolve_param_type(annotation: str) -> tuple[_ParamType, bool]:
    """Parse an annotation string -> ``(python_type, is_optional)``."""
    s = annotation.strip()
    is_optional = "None" in s
    s = s.replace("| None", "").replace("None |", "").replace("Optional[", "").rstrip("]").strip()
    return _TYPE_MAP.get(s, str), is_optional


def _add_tool_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    fn: Callable[..., Awaitable[str]],
) -> None:
    cmd = fn.__name__.replace("_", "-")
    doc_line = (fn.__doc__ or "").strip().splitlines()[0].replace("%", "%%") if fn.__doc__ else ""
    sub = subparsers.add_parser(cmd, help=doc_line)

    for pname, param in inspect.signature(fn).parameters.items():
        py_type, is_optional = _resolve_param_type(str(param.annotation))
        has_default = param.default is not inspect.Parameter.empty
        flag = f"--{pname.replace('_', '-')}"

        if py_type is bool:
            default_bool = bool(param.default) if has_default else False
            if default_bool:
                sub.add_argument(
                    f"--no-{pname.replace('_', '-')}",
                    dest=pname,
                    action="store_false",
                    default=True,
                )
            else:
                sub.add_argument(flag, action="store_true", default=False)
        elif has_default or is_optional:
            kw: dict[str, Any] = {"default": param.default if has_default else None}
            if py_type in (str, int, float):
                kw["type"] = py_type
            sub.add_argument(flag, **kw)
        else:
            kw = {}
            if py_type in (str, int, float):
                kw["type"] = py_type
            sub.add_argument(pname, **kw)


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, Callable[..., Awaitable[str]]]]:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description="rpm-mcp -- invoke a tool directly (CLI) or run as MCP server (default).",
    )
    subparsers = parser.add_subparsers(dest="tool", metavar="TOOL")
    subparsers.add_parser("serve", help="Run as MCP stdio server (same as invoking with no subcommand).")

    func_map: dict[str, Callable[..., Awaitable[str]]] = {}
    for fn in ALL_CLI_TOOLS:
        func_map[fn.__name__.replace("_", "-")] = fn
        _add_tool_subparser(subparsers, fn)
    return parser, func_map


async def _run_tool(fn: Callable[..., Awaitable[str]], kwargs: dict[str, Any]) -> None:
    suppress_untrusted_envelope()
    db_connected = False
    try:
        await db.connect()
        db_connected = True
    except Exception as exc:
        _logger.warning("db_unavailable_cli", error=str(exc))
    try:
        print(await fn(**kwargs))
    finally:
        await source_registry.close()
        if db_connected:
            await db.close()


def run_cli(serve: Callable[[], None]) -> None:
    """Dispatch: ``serve`` (default / explicit subcommand) or run a tool one-shot.

    ``serve`` is injected to keep this module free of FastMCP imports.
    """
    parser, func_map = build_parser()
    ns = parser.parse_args()

    if ns.tool in (None, "serve"):
        serve()
        return

    kwargs = {k.replace("-", "_"): v for k, v in vars(ns).items() if k != "tool"}
    asyncio.run(_run_tool(func_map[ns.tool], kwargs))

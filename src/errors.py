"""Typed exception hierarchy for rpm-mcp.

All public exceptions inherit from RPMMcpError so _tool_wrapper can dispatch
per type and emit actionable user-facing messages.

Existing SourceError and SourceNotFound in src/sources/base.py also inherit
from RPMMcpError so they are part of the hierarchy without a separate import.
"""

from __future__ import annotations


class RPMMcpError(Exception):
    """Base class for all rpm-mcp application exceptions."""


class ValidationError(RPMMcpError):
    """Invalid user input (package name, date, query parameter)."""


class DBError(RPMMcpError):
    """Database connection or query failure."""


class IngestError(RPMMcpError):
    """Ingestion pipeline failure that is not a source or DB error."""

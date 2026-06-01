"""Sanitize untrusted external content before storage and display.

Every parser fed by external sources (OBS, Gitea, Pagure, RPM db, news feeds)
should call ``scrub_external`` on raw text. The goal is to remove invisible /
out-of-band characters that could:

- inject ANSI escape sequences into terminal output
- truncate downstream display via null bytes
- smuggle a BOM that confuses parsers
- carry C0/C1 control bytes that some LLM tokenizers or terminals interpret

Tabs (\\t) and newlines (\\n) are preserved as they are structural.
"""

from __future__ import annotations

import re

import structlog

from .config import settings

_logger = structlog.get_logger("rpm-mcp.sanitize")

# CSI / OSC and other ANSI sequences: ESC followed by `[`/`]`/`(` etc.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07|[@-Z\\-_])")

# C0 controls except \t (0x09) and \n (0x0A); plus DEL (0x7F) and C1 (0x80-0x9F).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_BOM = "﻿"

# Phrases an attacker would use to redirect an LLM mid-context. Case-insensitive
# substring match — false positives expected in genuine security advisories,
# so this only logs; it never blocks or rewrites content.
_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "ignore the above",
    "disregard previous",
    "system:",
    "<|im_start|>",
    "<|im_end|>",
    "[inst]",
    "[/inst]",
    "### instruction",
    "### system",
)
_INJECTION_LOG_THRESHOLD = 2


_PREVIEW_BYTES = 80


def _scan_injection(text: str) -> tuple[int, list[str], str]:
    """Return ``(score, hits, preview)`` for the first marker context.

    The preview is the slice of *text* around the first matched marker,
    capped at ``_PREVIEW_BYTES``. Goes into the structured log so operators
    can triage without raw-text access.
    """
    lowered = text.lower()
    hits = [m for m in _INJECTION_MARKERS if m in lowered]
    preview = ""
    if hits:
        first = lowered.find(hits[0])
        start = max(0, first - _PREVIEW_BYTES // 2)
        end = min(len(text), first + _PREVIEW_BYTES // 2)
        preview = text[start:end].replace("\n", " ").strip()
    return len(hits), hits, preview


def scrub_external(
    text: str,
    *,
    max_bytes: int | None = None,
    source: str | None = None,
    package: str | None = None,
) -> str:
    """Strip ANSI escapes, null bytes, BOM, and other control chars from
    untrusted text, then truncate to ``max_bytes`` (UTF-8 encoded length).

    ``max_bytes`` defaults to ``settings.cache_max_entry_bytes``. Pass ``0``
    to disable truncation (rarely useful — embeddings and LLM context windows
    both have hard limits).

    If ``source`` / ``package`` are given and the text contains two or more
    LLM-redirection markers (``ignore previous``, ``<|im_start|>``, etc.),
    emits a ``possible_injection`` structlog warning. Content is returned
    unchanged — false positives in security advisories are too common to
    justify blocking.
    """
    if not text:
        return text
    if text.startswith(_BOM):
        text = text[len(_BOM) :]
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    if source is not None or package is not None:
        score, hits, preview = _scan_injection(text)
        if score >= _INJECTION_LOG_THRESHOLD:
            _logger.warning(
                "possible_injection",
                package=package,
                source=source,
                score=score,
                markers=hits,
                preview=preview,
            )
    cap = settings.cache_max_entry_bytes if max_bytes is None else max_bytes
    if cap and len(text.encode("utf-8")) > cap:
        encoded = text.encode("utf-8")[:cap]
        text = encoded.decode("utf-8", errors="ignore")
        text += f"\n[...truncated at {cap} bytes]"
    return text


# ---------------------------------------------------------------------------
# Secret scrubbing for log payloads
# ---------------------------------------------------------------------------
# Why: aiohttp / asyncpg exceptions can capture the full request URL or
# headers in their stringified form. If an upstream redirected with a
# query-string token, or a misconfigured DSN includes a password, that
# string lands verbatim in structlog. The DSN scrubber in Database is
# DSN-specific; this one is generic and applied wherever an exception is
# turned into a `error=...` log field.

_BASIC_AUTH_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+:[^/@\s]+@")
_QUERY_SECRET_RE = re.compile(
    r"(?P<key>(?:access_)?token|api[_-]?key|private[_-]?token|secret|password|"
    r"sig|signature|x[_-]api[_-]key)=([^&\s'\"]+)",
    re.IGNORECASE,
)
_HEADER_AUTH_RE = re.compile(
    r"(?P<key>authorization|private-token|x-api-key)\s*:\s*"
    r"(?:Bearer\s+|Basic\s+|Token\s+)?['\"]?[^'\",\s)]+",
    re.IGNORECASE,
)


def scrub_secrets(text: str) -> str:
    """Redact tokens, basic-auth credentials, and auth headers from *text*.

    Applied to exception messages before they are logged so accidental
    credential capture (URL with embedded token, redirected request with
    Bearer header in the error payload, etc.) does not land in structlog.
    Returns the input unchanged if no secret-shaped substring is present.
    """
    if not text:
        return text
    text = _BASIC_AUTH_RE.sub(r"\g<scheme>***:***@", text)
    text = _QUERY_SECRET_RE.sub(r"\g<key>=***", text)
    text = _HEADER_AUTH_RE.sub(r"\g<key>: ***", text)
    return text

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

from .config import settings

# CSI / OSC and other ANSI sequences: ESC followed by `[`/`]`/`(` etc.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07|[@-Z\\-_])")

# C0 controls except \t (0x09) and \n (0x0A); plus DEL (0x7F) and C1 (0x80-0x9F).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_BOM = "﻿"


def scrub_external(text: str, *, max_bytes: int | None = None) -> str:
    """Strip ANSI escapes, null bytes, BOM, and other control chars from
    untrusted text, then truncate to ``max_bytes`` (UTF-8 encoded length).

    ``max_bytes`` defaults to ``settings.cache_max_entry_bytes``. Pass ``0``
    to disable truncation (rarely useful — embeddings and LLM context windows
    both have hard limits).
    """
    if not text:
        return text
    if text.startswith(_BOM):
        text = text[len(_BOM):]
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    cap = settings.cache_max_entry_bytes if max_bytes is None else max_bytes
    if cap and len(text.encode("utf-8")) > cap:
        encoded = text.encode("utf-8")[:cap]
        text = encoded.decode("utf-8", errors="ignore")
        text += f"\n[...truncated at {cap} bytes]"
    return text

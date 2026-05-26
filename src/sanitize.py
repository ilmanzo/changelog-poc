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

# CSI / OSC and other ANSI sequences: ESC followed by `[`/`]`/`(` etc.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07|[@-Z\\-_])")

# C0 controls except \t (0x09) and \n (0x0A); plus DEL (0x7F) and C1 (0x80-0x9F).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

_BOM = "﻿"


def scrub_external(text: str) -> str:
    """Strip ANSI escapes, null bytes, BOM, and other control chars from
    untrusted text. Returns a safe-to-display, safe-to-embed string.
    """
    if not text:
        return text
    if text.startswith(_BOM):
        text = text[len(_BOM):]
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text

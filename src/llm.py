"""Async client for the local OpenAI-compatible LLM proxy.

Default endpoint and model come from ``settings`` (LLM_BASE_URL, LLM_MODEL).
Used by analyze_package, modernize_package, explain_build, and news classification.
"""
from __future__ import annotations

import httpx
import structlog

from .config import settings

_logger = structlog.get_logger("rpm-mcp.llm")

SYSTEM_PROMPT = (
    "You are a senior systems engineer specializing in RPM packaging "
    "(Fedora and openSUSE)."
)


async def ask_llm(question: str, context: str) -> str:
    """Send a context-grounded question to the LLM proxy. Returns answer text
    or a human-readable error string (never raises).
    """
    prompt = (
        "You are an expert in RPM packaging and Linux distributions "
        "(Fedora, openSUSE). Use the following context to answer the user's "
        "question accurately. If the context doesn't contain the answer, say "
        "you don't know based on the current database.\n\n"
        f"Context:\n{context}\n\nQuestion: {question}"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(
                f"{settings.llm_base_url}/v1/chat/completions",
                json={
                    "model": settings.llm_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                },
            )
            if resp.status_code != 200:
                return f"Error: LLM returned {resp.status_code} — {resp.text[:200]}"
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        _logger.error("llm_error", error=str(e))
        return f"Error connecting to LLM: {e}"

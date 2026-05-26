"""Unit tests for src/llm.py — mock httpx.AsyncClient."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import SYSTEM_PROMPT, _build_prompt, ask_llm


def _mock_client(status_code: int = 200, body: dict | None = None, side_effect: Exception | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error text"
    if body:
        resp.json.return_value = body

    client = AsyncMock()
    if side_effect:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


_SUCCESS_BODY = {"choices": [{"message": {"content": "The answer is 42."}}]}


async def test_ask_llm_success_returns_content() -> None:
    mock_client = _mock_client(200, _SUCCESS_BODY)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        result = await ask_llm("What is vim?", "Context: vim is an editor.")
    assert result == "The answer is 42."


async def test_ask_llm_sends_question_in_prompt() -> None:
    mock_client = _mock_client(200, _SUCCESS_BODY)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        await ask_llm("Explain %build section", "Spec: ...")

    _, kwargs = mock_client.post.call_args
    payload = kwargs.get("json") or mock_client.post.call_args[0][1]
    # The user message should contain the question
    messages = mock_client.post.call_args.kwargs["json"]["messages"]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "Explain %build section" in user_message


def _err_client_factory(case: str):
    import httpx
    if case == "http_500":
        return _mock_client(status_code=500)
    if case == "connect_error":
        return _mock_client(side_effect=httpx.ConnectError("refused"))
    if case == "timeout":
        return _mock_client(side_effect=httpx.TimeoutException("timeout"))
    raise ValueError(case)


@pytest.mark.parametrize("case", ["http_500", "connect_error", "timeout"])
async def test_ask_llm_error_returns_error_string(case: str) -> None:
    mock_client = _err_client_factory(case)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        result = await ask_llm("question", "context")
    assert "Error" in result or "500" in result


# --- S7(a) prompt-injection hardening ---


def test_system_prompt_has_security_rules() -> None:
    """System prompt must instruct model to treat CONTEXT as data only."""
    assert "SECURITY RULES" in SYSTEM_PROMPT
    assert "untrusted" in SYSTEM_PROMPT.lower()
    assert "ignore" in SYSTEM_PROMPT.lower()


def test_build_prompt_fences_context_with_nonce() -> None:
    prompt = _build_prompt("What is X?", "ATTACKER PAYLOAD")
    m = re.search(r"<<UNTRUSTED_DATA_BEGIN_([0-9a-f]{16})>>", prompt)
    assert m, "missing nonce-delimited begin marker"
    nonce = m.group(1)
    assert f"<<UNTRUSTED_DATA_END_{nonce}>>" in prompt
    assert "ATTACKER PAYLOAD" in prompt
    assert "QUESTION: What is X?" in prompt


def test_build_prompt_uses_fresh_nonce_each_call() -> None:
    a = _build_prompt("q", "ctx")
    b = _build_prompt("q", "ctx")
    nonce_re = re.compile(r"UNTRUSTED_DATA_BEGIN_([0-9a-f]{16})")
    na = nonce_re.search(a)
    nb = nonce_re.search(b)
    assert na and nb
    assert na.group(1) != nb.group(1)


async def test_ask_llm_user_message_contains_fence() -> None:
    mock_client = _mock_client(200, _SUCCESS_BODY)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        await ask_llm("explain", "untrusted body")
    messages = mock_client.post.call_args.kwargs["json"]["messages"]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "UNTRUSTED_DATA_BEGIN_" in user_message
    assert "UNTRUSTED_DATA_END_" in user_message

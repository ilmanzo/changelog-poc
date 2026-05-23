"""Unit tests for src/llm.py — mock httpx.AsyncClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm import ask_llm


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


async def test_ask_llm_http_error_returns_error_string() -> None:
    mock_client = _mock_client(status_code=500)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        result = await ask_llm("question", "context")
    assert "Error" in result or "500" in result


async def test_ask_llm_connection_error_returns_error_string() -> None:
    import httpx
    mock_client = _mock_client(side_effect=httpx.ConnectError("refused"))
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        result = await ask_llm("question", "context")
    assert "Error" in result


async def test_ask_llm_timeout_returns_error_string() -> None:
    import httpx
    mock_client = _mock_client(side_effect=httpx.TimeoutException("timeout"))
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        result = await ask_llm("question", "context")
    assert "Error" in result

"""Unit tests for src/llm.py — mock httpx.AsyncClient."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.llm import LLMError, SYSTEM_PROMPT, _build_prompt, ask_llm


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip tenacity's exponential backoff in tests."""
    async def _instant(_seconds: float) -> None:
        return None
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None, raising=False)
    monkeypatch.setattr("asyncio.sleep", _instant)


def _mock_client(
    status_code: int = 200,
    body: dict | None = None,
    side_effect: Exception | list | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error text"
    if body:
        resp.json.return_value = body

    client = AsyncMock()
    if side_effect is not None:
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
    assert mock_client.post.call_count == 1


async def test_ask_llm_sends_question_in_prompt() -> None:
    mock_client = _mock_client(200, _SUCCESS_BODY)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        await ask_llm("Explain %build section", "Spec: ...")

    messages = mock_client.post.call_args.kwargs["json"]["messages"]
    user_message = next(m["content"] for m in messages if m["role"] == "user")
    assert "Explain %build section" in user_message


# --- P1 + DD2: retry + LLMError ---


async def test_ask_llm_raises_llm_error_on_4xx_no_retry() -> None:
    mock_client = _mock_client(status_code=400)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(LLMError, match="400"):
            await ask_llm("q", "c")
    # 4xx is non-retryable
    assert mock_client.post.call_count == 1


async def test_ask_llm_retries_3x_on_5xx_then_raises() -> None:
    mock_client = _mock_client(status_code=503)
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(LLMError, match="unreachable after 3 attempts"):
            await ask_llm("q", "c")
    assert mock_client.post.call_count == 3


async def test_ask_llm_retries_3x_on_connect_error_then_raises() -> None:
    mock_client = _mock_client(side_effect=httpx.ConnectError("refused"))
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(LLMError, match="unreachable after 3 attempts"):
            await ask_llm("q", "c")
    assert mock_client.post.call_count == 3


async def test_ask_llm_retries_3x_on_timeout_then_raises() -> None:
    mock_client = _mock_client(side_effect=httpx.TimeoutException("slow"))
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(LLMError, match="unreachable after 3 attempts"):
            await ask_llm("q", "c")
    assert mock_client.post.call_count == 3


async def test_ask_llm_recovers_on_second_attempt() -> None:
    """Transient connect error on attempt 1, success on attempt 2."""
    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.json.return_value = _SUCCESS_BODY

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[httpx.ConnectError("transient"), good_resp])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.llm.httpx.AsyncClient", return_value=client):
        result = await ask_llm("q", "c")
    assert result == "The answer is 42."
    assert client.post.call_count == 2


async def test_ask_llm_raises_on_malformed_response() -> None:
    mock_client = _mock_client(200, body={"unexpected": "shape"})
    with patch("src.llm.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(LLMError, match="malformed"):
            await ask_llm("q", "c")
    # Malformed payload is not retried
    assert mock_client.post.call_count == 1


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

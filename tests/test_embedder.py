"""Unit tests for src/embedder.py.

chunk_text() is a pure function — tested directly.
embed_one() / embed_batch() patch _get_model to avoid loading ONNX weights.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.embedder import chunk_text, embed_batch, embed_one


# ---------------------------------------------------------------------------
# Fake vector — avoids numpy dependency in tests
# ---------------------------------------------------------------------------
class _FakeVec:
    def __init__(self, data: list[float]) -> None:
        self._data = data

    def tolist(self) -> list[float]:
        return self._data


_VEC_384 = [0.1] * 384


# ---------------------------------------------------------------------------
# chunk_text — pure function, settings-driven (chunk_size=1000, overlap=100)
# ---------------------------------------------------------------------------
def test_chunk_text_short_returns_single() -> None:
    text = "Short text under 1000 chars"
    chunks = chunk_text(text)
    assert chunks == [text]


def test_chunk_text_exactly_at_limit() -> None:
    text = "x" * 1000
    chunks = chunk_text(text)
    assert chunks == [text]


def test_chunk_text_long_returns_multiple() -> None:
    # 2200 chars → at least 3 chunks with step=900
    text = "a" * 2200
    chunks = chunk_text(text)
    assert len(chunks) >= 3


def test_chunk_text_overlap_present() -> None:
    # First chunk ends at 1000; second starts at 900 — 100-char overlap
    text = "a" * 1100
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0][900:] == chunks[1][:100]  # 100-char overlap


def test_chunk_text_each_chunk_max_1000() -> None:
    text = "z" * 5000
    chunks = chunk_text(text)
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_text_empty_string() -> None:
    chunks = chunk_text("")
    assert chunks == [""]


# ---------------------------------------------------------------------------
# embed_one — patch _get_model
# ---------------------------------------------------------------------------
async def test_embed_one_returns_vector() -> None:
    # embed() is synchronous in fastembed — use MagicMock, not AsyncMock
    mock_model = MagicMock()
    mock_model.embed.return_value = [_FakeVec(_VEC_384)]
    with patch("src.embedder._get_model", new=AsyncMock(return_value=mock_model)):
        result = await embed_one("hello world")
    assert result == _VEC_384
    assert len(result) == 384


async def test_embed_one_returns_empty_on_failure() -> None:
    mock_model = MagicMock()
    mock_model.embed.side_effect = RuntimeError("ONNX failure")
    with patch("src.embedder._get_model", new=AsyncMock(return_value=mock_model)):
        result = await embed_one("test")
    assert result == []


# ---------------------------------------------------------------------------
# embed_batch — patch _get_model
# ---------------------------------------------------------------------------
async def test_embed_batch_returns_list_of_vectors() -> None:
    texts = ["first", "second", "third"]
    mock_model = MagicMock()
    mock_model.embed.return_value = [_FakeVec(_VEC_384)] * 3
    with patch("src.embedder._get_model", new=AsyncMock(return_value=mock_model)):
        results = await embed_batch(texts)
    assert len(results) == 3
    assert all(len(v) == 384 for v in results)


async def test_embed_batch_empty_input_returns_empty() -> None:
    result = await embed_batch([])
    assert result == []


async def test_embed_batch_returns_empty_on_failure() -> None:
    mock_model = MagicMock()
    mock_model.embed.side_effect = RuntimeError("ONNX failure")
    with patch("src.embedder._get_model", new=AsyncMock(return_value=mock_model)):
        result = await embed_batch(["text"])
    assert result == []

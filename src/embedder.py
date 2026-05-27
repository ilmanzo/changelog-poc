"""Local fastembed wrapper.

One module-level singleton avoids reloading the ONNX model per call. Used by
both the ingest path (batched) and the query path (single-string).
"""
from __future__ import annotations

import asyncio
from typing import Iterable

import structlog
from fastembed import TextEmbedding

from .config import settings

_logger = structlog.get_logger("rpm-mcp.embedder")
_model: TextEmbedding | None = None
_lock = asyncio.Lock()


async def _get_model() -> TextEmbedding:
    global _model
    if _model is not None:
        return _model
    async with _lock:
        if _model is None:
            _logger.info("loading_embedding_model", model=settings.embedding_model or "<default>")
            # fastembed downloads on first use and caches under ~/.cache.
            _model = (
                TextEmbedding(model_name=settings.embedding_model)
                if settings.embedding_model
                else TextEmbedding()
            )
        return _model


async def embed_one(text: str) -> list[float]:
    """Embed a single string. Returns an empty list on failure."""
    try:
        model = await _get_model()
        vecs = list(model.embed([text]))
        return vecs[0].tolist() if vecs else []
    except Exception as e:
        _logger.error("embed_one_failed", error=str(e))
        return []


async def embed_batch(texts: Iterable[str]) -> list[list[float]]:
    """Embed a batch of strings. Empty list on failure."""
    texts = list(texts)
    if not texts:
        return []
    try:
        model = await _get_model()
        return [v.tolist() for v in model.embed(texts, batch_size=settings.embedding_batch_size)]
    except Exception as e:
        _logger.error("embed_batch_failed", error=str(e), count=len(texts))
        return []


def chunk_text(text: str) -> list[str]:
    """Sliding-window chunking for long sections, settings-driven."""
    size = settings.embedding_chunk_size
    overlap = settings.embedding_chunk_overlap
    if len(text) <= size:
        return [text]
    step = size - overlap
    positions = list(range(0, len(text) - size + 1, step))
    last_start = positions[-1] if positions else -1
    if last_start + size < len(text):
        positions.append(len(text) - size)
    return [text[i : i + size] for i in positions]

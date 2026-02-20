"""Embedding and FAISS index for semantic tool search."""

from __future__ import annotations

import logging

import faiss
import numpy as np
from mcp import types
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


def tool_to_text(tool: types.Tool) -> str:
    """Convert a tool schema into a single text string for embedding.

    Concatenates the tool name, description, and parameter names/descriptions
    to give the embedding model maximum semantic signal.
    """
    parts = [tool.name.replace("__", " ").replace("_", " ")]

    if tool.description:
        parts.append(tool.description)

    props = tool.inputSchema.get("properties", {})
    for param_name, param_info in props.items():
        param_text = param_name.replace("_", " ")
        if isinstance(param_info, dict) and param_info.get("description"):
            param_text += f": {param_info['description']}"
        parts.append(param_text)

    return " ".join(parts)


class EmbeddingIndex:
    """Loads a sentence-transformers model for tool embedding and search."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        logger.info("Loading embedding model: %s", model_name)
        self._model = SentenceTransformer(model_name)
        logger.info("Embedding model loaded")
        self._index: faiss.IndexFlatIP | None = None
        self._tools: list[types.Tool] = []

    def build_index(self, tools: list[types.Tool]) -> None:
        """Embed all tools and build a FAISS inner-product index."""
        self._tools = list(tools)
        texts = [tool_to_text(t) for t in self._tools]
        embeddings = self._model.encode(texts, convert_to_numpy=True)
        embeddings = embeddings.astype(np.float32)
        faiss.normalize_L2(embeddings)
        dimension = embeddings.shape[1]
        self._index = faiss.IndexFlatIP(dimension)
        self._index.add(embeddings)
        logger.info("Built FAISS index with %d tools (dim=%d)", len(self._tools), dimension)

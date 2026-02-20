"""Embedding and FAISS index for semantic tool search."""

from __future__ import annotations

import logging

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

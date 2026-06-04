import logging
import os
import threading
from typing import List

import requests

from src.config import cfg

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL",    "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "qwen3-embedding:8b")

# Instruction prefix for query-side encoding (asymmetric retrieval)
QUERY_INSTRUCTION = (
    "Instruct: Given a user question, retrieve relevant passages that answer it\nQuery: "
)


class MxbaiEmbedder:
    """Ollama-backed embedder for query-side encoding in the retrieval service."""

    def __init__(self, model_name: str = None):
        self._model      = model_name or OLLAMA_EMBED_MODEL
        self._base_url   = OLLAMA_BASE_URL
        self._lock       = threading.Lock()
        self.embedding_dimension = getattr(cfg, "embedding_dim", 4096)
        logger.info("MxbaiEmbedder: Ollama model='%s' url='%s' dim=%d",
                    self._model, self._base_url, self.embedding_dimension)

    def _call(self, texts: List[str]) -> List[List[float]]:
        payload = {"model": self._model, "input": texts}
        try:
            resp = requests.post(
                f"{self._base_url}/api/embed",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("embeddings", [])
        except Exception as e:
            logger.error("Ollama embed failed: %s", e)
            raise RuntimeError(f"Embedding service unavailable: {e}") from e

    def embed_text(self, text: str) -> List[float]:
        """Embed a query string with the retrieval instruction prefix."""
        with self._lock:
            vecs = self._call([QUERY_INSTRUCTION + text])
        if not vecs:
            raise RuntimeError("Embedding model returned no vector")
        return vecs[0]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        with self._lock:
            return self._call(texts)

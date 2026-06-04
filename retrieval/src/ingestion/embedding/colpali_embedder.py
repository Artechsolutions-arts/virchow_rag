"""
ColPaliEmbedder (retrieval side)
================================
CPU-only variant used at query time to encode text queries through
ColPali's text tower, producing a 128-dim vector for similarity search
against colpali_page_embeddings stored in pgvector.

We only call embed_query() here — page embedding happens in the ingest
pipeline. Loading is lazy and thread-safe.
"""

import logging
import threading
from typing import List

logger = logging.getLogger(__name__)

COLPALI_MODEL = "vidore/colpali-v1.2"
COLPALI_DIM = 128


class ColPaliEmbedder:
    """Lazy-loading ColPali query encoder. CPU-only in the retrieval service."""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self, model_name: str = COLPALI_MODEL):
        self._model_name = model_name
        self._model = None
        self._processor = None
        self._load_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "ColPaliEmbedder":
        """Singleton accessor — one model per retrieval process."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                from colpali_engine.models import ColPali, ColPaliProcessor
                import torch
                logger.info("Loading ColPali query encoder '%s' on CPU", self._model_name)
                model = ColPali.from_pretrained(
                    self._model_name,
                    torch_dtype=torch.float32,
                    device_map="cpu",
                ).eval()
                processor = ColPaliProcessor.from_pretrained(self._model_name)
                self._model = model
                self._processor = processor
                logger.info("ColPali query encoder loaded (dim=%d)", COLPALI_DIM)
            except Exception as e:
                logger.error("ColPali query encoder load failed: %s", e)

    def embed_query(self, text: str) -> List[float]:
        """
        Encode a text query through ColPali's text tower.
        Returns a L2-normalised 128-dim vector, or zeros on failure.
        """
        self._ensure_loaded()
        if self._model is None:
            return [0.0] * COLPALI_DIM
        try:
            import torch
            batch = self._processor.process_queries([text]).to("cpu")
            with torch.no_grad():
                output = self._model(**batch)  # (1, seq_len, 128)
            vec = output.mean(dim=1)[0]        # (128,)
            vec = vec / (vec.norm() + 1e-8)
            return vec.float().cpu().tolist()
        except Exception as e:
            logger.error("ColPali embed_query failed: %s", e)
            return [0.0] * COLPALI_DIM

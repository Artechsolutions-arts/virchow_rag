"""
ColPaliEmbedder
===============
Wraps vidore/colpali-v1.2 for visual page embeddings.

ColPali produces patch-level embeddings (batch × seq_len × 128).
We mean-pool the patch dimension to get one 128-dim vector per page,
then L2-normalise so cosine similarity works correctly in pgvector.

On Apple MPS, bfloat16 tensors must be cast to float16 — same
workaround used by DotsOCR in the rest of this pipeline.
"""

import logging
import threading
from typing import List

from PIL import Image

logger = logging.getLogger(__name__)

COLPALI_MODEL = "vidore/colpali-v1.2"
COLPALI_DIM = 128


class ColPaliEmbedder:
    """Lazy-loading wrapper around ColPali. Thread-safe single-load."""

    def __init__(self, model_name: str = COLPALI_MODEL, device: str = None):
        self._model_name = model_name
        self._device = device or self._pick_device()
        self._model = None
        self._processor = None
        self._load_lock = threading.Lock()

    # ── Device selection ──────────────────────────────────────────────────────

    @staticmethod
    def _pick_device() -> str:
        try:
            import torch
            if torch.backends.mps.is_available():
                return "mps"
        except Exception:
            pass
        return "cpu"

    # ── Lazy load ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            self._load(self._device)

    def _load(self, device: str):
        try:
            from colpali_engine.models import ColPali, ColPaliProcessor
            import torch

            logger.info("Loading ColPali '%s' on %s", self._model_name, device)
            dtype = torch.float16 if device == "mps" else torch.float32
            model = ColPali.from_pretrained(
                self._model_name,
                torch_dtype=dtype,
                device_map=device,
            ).eval()

            # MPS safety: bfloat16 buffers crash MPS — cast them to float16
            if device == "mps":
                for module in model.modules():
                    for name, buf in list(module.named_buffers(recurse=False)):
                        if buf is not None and buf.dtype == torch.bfloat16:
                            module.register_buffer(name, buf.to(torch.float16))

            processor = ColPaliProcessor.from_pretrained(self._model_name)
            self._model = model
            self._processor = processor
            self._device = device
            logger.info("ColPali loaded (dim=%d, device=%s)", COLPALI_DIM, device)

        except Exception as e:
            logger.error("ColPali load failed on '%s': %s", device, e)
            if device != "cpu":
                logger.info("ColPali retrying on CPU…")
                try:
                    from colpali_engine.models import ColPali, ColPaliProcessor
                    import torch
                    model = ColPali.from_pretrained(
                        self._model_name,
                        torch_dtype=torch.float32,
                        device_map="cpu",
                    ).eval()
                    processor = ColPaliProcessor.from_pretrained(self._model_name)
                    self._model = model
                    self._processor = processor
                    self._device = "cpu"
                    logger.info("ColPali loaded on CPU fallback")
                except Exception as e2:
                    logger.error("ColPali CPU fallback failed: %s", e2)

    # ── Public API ────────────────────────────────────────────────────────────

    def embed_pages(self, images: List[Image.Image]) -> List[List[float]]:
        """
        Embed a list of page images.
        Returns one L2-normalised 128-dim vector per image.
        """
        self._ensure_loaded()
        if self._model is None:
            logger.warning("ColPali not loaded — returning zero vectors")
            return [[0.0] * COLPALI_DIM for _ in images]

        if not images:
            return []

        try:
            import torch
            batch = self._processor.process_images(images).to(self._device)
            with torch.no_grad():
                output = self._model(**batch)  # (B, seq_len, 128)
            # Mean-pool patch tokens → (B, 128)
            vecs = output.mean(dim=1)
            vecs = vecs / (vecs.norm(dim=-1, keepdim=True) + 1e-8)
            return vecs.float().cpu().tolist()
        except Exception as e:
            logger.error("ColPali embed_pages failed: %s", e)
            return [[0.0] * COLPALI_DIM for _ in images]

    def embed_query(self, text: str) -> List[float]:
        """
        Encode a text query through ColPali's text tower.
        Returns a L2-normalised 128-dim vector.
        """
        self._ensure_loaded()
        if self._model is None:
            return [0.0] * COLPALI_DIM

        try:
            import torch
            batch = self._processor.process_queries([text]).to(self._device)
            with torch.no_grad():
                output = self._model(**batch)  # (1, seq_len, 128)
            vec = output.mean(dim=1)[0]       # (128,)
            vec = vec / (vec.norm() + 1e-8)
            return vec.float().cpu().tolist()
        except Exception as e:
            logger.error("ColPali embed_query failed: %s", e)
            return [0.0] * COLPALI_DIM

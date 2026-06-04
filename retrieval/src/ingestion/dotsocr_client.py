"""
DotsOCR client — local HuggingFace inference (no vLLM server required).

Loads the DotsOCR vision-language model in-process via transformers.
Model weights are read from cfg.dotsocr_weights_path (DOTSOCR_WEIGHTS_PATH env var).

Public API (unchanged from the previous vLLM version):
  check_vllm_health()  → bool        (kept for backward compat — just loads the model)
  ocr_pdf(pdf_bytes)   → list[str]   (list of HTML strings, one per page)
"""

import json
import logging
import os
import tempfile

from src.config import cfg

logger = logging.getLogger(__name__)

_parser = None  # lazy singleton — loaded on first use


def _get_parser():
    global _parser
    if _parser is None:
        from dots_ocr.parser import DotsOCRParser
        weights = cfg.dotsocr_weights_path
        if not os.path.exists(weights):
            raise RuntimeError(
                f"DotsOCR weights not found at '{weights}'. "
                "Set DOTSOCR_WEIGHTS_PATH to the directory containing the model safetensors."
            )
        logger.info("[DotsOCR] Loading model from %s (HF mode) ...", weights)
        _parser = DotsOCRParser(model_name=weights, use_hf=True, dpi=200)
        logger.info("[DotsOCR] Model ready.")
    return _parser


def check_model_ready() -> bool:
    """Load the model eagerly. Raises RuntimeError if weights are missing."""
    _get_parser()
    return True


def check_vllm_health(timeout: int = 60) -> bool:
    """Backward-compat alias for check_model_ready(). timeout param is ignored."""
    return check_model_ready()


def _cells_to_html(cells: list) -> str:
    """Convert DotsOCR layout JSON cells to an HTML string for html_table_parser."""
    parts = []
    for cell in cells:
        category = cell.get("category", "")
        text = cell.get("text", "")
        if not text:
            continue
        if category == "Table":
            parts.append(text)  # already HTML from the model
        elif category != "Picture":
            parts.append(f"<p>{text}</p>")
    return "\n".join(parts)


def ocr_page_image(pil_image) -> str:
    """
    OCR a single PIL Image page.
    Returns an HTML string suitable for html_table_parser.
    """
    from dots_ocr.utils.prompts import dict_promptmode_to_prompt

    parser = _get_parser()
    prompt = dict_promptmode_to_prompt["prompt_layout_all_en"]

    try:
        response = parser._inference_with_hf(pil_image, prompt)
        try:
            cells = json.loads(response)
            if isinstance(cells, list):
                return _cells_to_html(cells)
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: model returned plain text instead of JSON
        return f"<p>{response}</p>"
    except Exception as e:
        logger.error("[DotsOCR] Page OCR failed: %s", e)
        return ""


def ocr_pdf(pdf_bytes: bytes, dpi: int = 200) -> list:
    """
    OCR all pages of a PDF. Returns a list of HTML strings, one per page.
    """
    from dots_ocr.utils.doc_utils import load_images_from_pdf

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name

    try:
        pages = load_images_from_pdf(tmp_path, dpi=dpi)
    finally:
        os.unlink(tmp_path)

    results = []
    for i, pil_image in enumerate(pages, start=1):
        logger.debug("[DotsOCR] OCR page %d/%d ...", i, len(pages))
        results.append(ocr_page_image(pil_image))

    logger.info("[DotsOCR] OCR complete: %d pages", len(results))
    return results

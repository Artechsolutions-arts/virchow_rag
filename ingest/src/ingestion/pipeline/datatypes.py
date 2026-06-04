"""
Pipeline Datatypes
==================
Immutable data classes that flow between pipeline stages.

Flow:
  DocJob → [Preprocessing] → PageJob → [OCR] → PageResult
        → [Layout+Markdown] → PageMarkdown → [Assembly]
        → AssembledDoc → [ColPali] → AssembledDoc → [Chunking] → ChunkItem (per chunk)
        → [EmbeddingBatcher] → EmbeddedItem → [Storage]
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image


# ── Stage 1 input ────────────────────────────────────────────────────────────

@dataclass
class DocJob:
    """A document submitted to the pipeline."""
    file_id:      str
    session_id:   str
    filename:     str
    # Path on local disk where the PDF is stored.  Raw bytes are NOT kept in
    # the DocJob so that _doc_q can hold thousands of entries without consuming
    # gigabytes of RAM.  Each preprocess worker reads from disk only when it
    # dequeues a job — at most N_PREPROCESS files in memory at once.
    file_path:    str
    user_id:      str
    dept_id:      str
    upload_id:    Optional[str] = None
    upload_type:  str = "user"
    # SHA-256 of the PDF bytes, computed in the preprocess worker.
    content_hash: str = ""
    # DB document id of the pending placeholder row, set in preprocess worker
    # and propagated downstream so all stage workers can update current_stage.
    db_doc_id:    Optional[str] = None
    file_size_kb: float = 0.0


# ── Stage 1 → Stage 2 ────────────────────────────────────────────────────────

@dataclass
class PageJob:
    """One preprocessed page ready for OCR."""
    file_id:      str
    session_id:   str
    page_idx:     int
    total_pages:  int
    image:        Image.Image   # Enhanced PIL image for DotsOCR
    origin_image: Image.Image   # Original (pre-enhancement) for bbox mapping
    doc_job:      DocJob


# ── Stage 2 → Stage 3 ────────────────────────────────────────────────────────

@dataclass
class PageOCRResult:
    """Raw OCR JSON response for one page."""
    file_id:      str
    session_id:   str
    page_idx:     int
    total_pages:  int
    ocr_response: str           # Raw JSON string from DotsOCR model.generate()
    image:        Image.Image   # Needed for post_process_output bbox mapping
    origin_image: Image.Image
    doc_job:      DocJob
    error:        Optional[str] = None


# ── Stage 3 → Stage 4 ────────────────────────────────────────────────────────

@dataclass
class PageMarkdown:
    """Parsed layout + generated Markdown for one page."""
    file_id:     str
    session_id:  str
    page_idx:    int
    total_pages: int
    markdown:    str            # Final markdown text for this page
    doc_job:     DocJob
    error:       Optional[str] = None


# ── Stage 4 → Stage 5 ────────────────────────────────────────────────────────

@dataclass
class AssembledDoc:
    """All pages collected and assembled into one Markdown document."""
    file_id:           str
    session_id:        str
    markdown:          str
    page_count:        int
    content_hash:      str
    doc_job:           DocJob
    # Page indices (0-based) flagged for ColPali (have embedded images or low text).
    visual_page_nums:  list = field(default_factory=list)
    # ColPali vectors keyed by page_num — populated by colpali_worker when doc_id
    # is not yet available (admin uploads). The chunk_worker stores them after
    # creating the document record.
    colpali_vectors:   dict = field(default_factory=dict)


# ── Stage 5 → Stage 6 ────────────────────────────────────────────────────────

@dataclass
class ChunkItem:
    """One chunk from a document, ready for embedding."""
    file_id:      str
    session_id:   str
    chunk_idx:    int
    total_chunks: int
    content:      str
    metadata:     dict
    content_hash: str
    doc_job:      DocJob
    # Accurate tiktoken token count from the chunker (not a word-split approximation)
    token_count:  int = 0


# ── Stage 6 → Stage 7 ────────────────────────────────────────────────────────

@dataclass
class EmbeddedItem:
    """One chunk with its embedding vector, ready for storage."""
    chunk_item: ChunkItem
    embedding:  list[float]

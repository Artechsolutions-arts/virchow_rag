from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RetrievedChunk:
    chunk_id:   str   = ""
    chunk_text: str   = ""
    file_name:  str   = ""
    page_num:   int   = 0
    similarity: float = 0.0

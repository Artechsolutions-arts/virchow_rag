#!/bin/bash
# One-time setup: creates a macOS-native Python venv for the ingest worker.
# Run this once from the ingest/ directory:
#   cd /Users/macai/Desktop/virchow_rag/ingest && bash setup_native.sh
#
# The native venv uses torch with MPS support (Apple Silicon GPU).
# Docker ingest-worker uses CPU; this venv gives ~5-10x faster OCR/embed.

set -e
cd "$(dirname "$0")"

VENV=venv_native

# Python 3.14 (default on newer macOS) only has torch>=2.9 in PyPI, but
# colpali-engine==0.3.10 requires torch<2.7.0. Use Python 3.10 where
# torch 2.6.0 is available and the full dependency chain resolves cleanly.
PYTHON=python3.10
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found. Install with: brew install python@3.10"
    exit 1
fi

echo "=== Creating venv with $PYTHON ==="
"$PYTHON" -m venv "$VENV"
source "$VENV/bin/activate"

echo "=== Upgrading pip ==="
pip install --upgrade pip setuptools wheel

echo "=== Installing PyTorch (macOS MPS-enabled, pinned to compatible pair) ==="
# Pin torch==2.6.0 + torchvision==0.21.0 — the versions that satisfy
# colpali-engine==0.3.10 (requires torch>=2.5.0,<2.7.0).
# Do NOT use --extra-index-url pytorch.org/whl/cpu — those are Linux
# CPU-only wheels. macOS PyPI wheels include MPS support by default.
pip install "torch==2.6.0" "torchvision==0.21.0"

echo "=== Installing core requirements (excluding ML heavies) ==="
grep -vE 'torch|torchvision|transformers|sentence-transformers|qwen-vl-utils|modelscope|huggingface|colpali|accelerate' \
    requirements.txt > /tmp/req_core.txt
pip install -r /tmp/req_core.txt

echo "=== Installing transformers + sentence-transformers ==="
pip install \
    "transformers==4.51.3" \
    "sentence-transformers>=2.7.0" \
    "huggingface-hub>=0.30.0,<1.0"

echo "=== Installing colpali-engine (requires torch<2.7.0) ==="
pip install "colpali-engine==0.3.10"

echo "=== Installing DotsOCR support libs ==="
pip install \
    "qwen-vl-utils" \
    "openai>=1.0.0,<2.0.0" \
    "accelerate>=0.26.0" \
    "modelscope>=1.18.0,<2.0.0" \
    "aiohttp>=3.8.0,<4.0.0" \
    "tqdm>=4.65.0,<5.0.0"

echo "=== Pinning torchvision to compatible version (modelscope may upgrade it) ==="
pip install --force-reinstall "torch==2.6.0" "torchvision==0.21.0"

echo ""
echo "=== Verifying MPS availability ==="
python3 -c "
import torch
print(f'torch:         {torch.__version__}')
print(f'MPS available: {torch.backends.mps.is_available()}')
print(f'MPS built:     {torch.backends.mps.is_built()}')
if torch.backends.mps.is_available():
    t = torch.ones(3, device='mps')
    print(f'MPS test:      {t.sum().item()} (should be 3.0)')
    print('MPS is READY')
else:
    print('WARNING: MPS not available — will fall back to CPU')
"

echo ""
echo "=== Setup complete ==="
echo "To start the worker:  bash run_native.sh"

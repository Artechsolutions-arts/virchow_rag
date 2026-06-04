#!/bin/bash
# Run the ingest worker natively on macOS (Apple Silicon).
# DotsOCR runs on MPS (HuggingFace); Ollama serves embeddings.
#
# Prerequisites:
#   1. bash setup_native.sh                               (one-time venv creation)
#   2. ollama pull qwen3-embedding:8b
#   3. docker compose up -d redis rabbitmq ingest-api     (keep these in Docker)
#   4. docker compose stop ingest-worker                  (stop Docker worker)
#
# Then: cd /Users/macai/Desktop/virchow_rag/ingest && bash run_native.sh

set -e
cd "$(dirname "$0")"

VENV=venv_native
if [ ! -f "$VENV/bin/activate" ]; then
    echo "ERROR: venv not found. Run:  bash setup_native.sh"
    exit 1
fi

source "$VENV/bin/activate"

# ── Infrastructure (Docker containers on same host) ──────────────────────────
export REDIS_HOST=localhost
export REDIS_PORT=6379
export RABBIT_HOST=localhost
export RABBIT_PORT=5672
export RABBIT_USER=guest
export RABBIT_PASS=guest
export RABBIT_VHOST=/

# ── Database ──────────────────────────────────────────────────────────────────
export PG_HOST=192.168.10.10
export PG_PORT=5433
export PG_DATABASE=virchow_dev
export PG_USER=postgres
export PG_PASSWORD='Eppl$456!'

# ── Object storage ────────────────────────────────────────────────────────────
export SEAWEEDFS_MASTER_URL=http://192.168.10.10:9333
export SEAWEEDFS_FILER_URL=http://192.168.10.10:8889
export SEAWEEDFS_S3_ENDPOINT=http://192.168.10.10:8333
export SEAWEEDFS_BUCKET=rag-docs
export SEAWEEDFS_ACCESS_KEY=anykey
export SEAWEEDFS_SECRET_KEY=anysecret

# ── DotsOCR (HuggingFace, loads on MPS) ──────────────────────────────────────
export DOTS_OCR_USE_HF=true
export DOTS_OCR_MODEL=rednote-hilab/dots.ocr
export DOTS_OCR_WEIGHTS=../weights/DotsOCR
export DOTS_OCR_PROMPT_MODE=prompt_layout_all_en

# ── Ollama (embedding only) ───────────────────────────────────────────────────
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_EMBED_MODEL=qwen3-embedding:8b
export EMBEDDING_DIM=4096

# ── Sequential pipeline workers ───────────────────────────────────────────────
# Each worker processes one document fully before picking up the next.
# DotsOCR runs on MPS (blocking), so 1-2 workers share the same GPU.
export N_SEQ_WORKERS=4
export SEQ_QUEUE_SIZE=0
export UPLOAD_WORKERS=6

# ── Python path ───────────────────────────────────────────────────────────────
export PYTHONPATH="$(pwd):$(pwd)/.."

export LOG_LEVEL=INFO
export RUN_TYPE=worker

# ── Pre-flight checks ─────────────────────────────────────────────────────────
echo "=== Pre-flight: DotsOCR weights check ==="
if [ -d "../weights/DotsOCR" ]; then
    echo "  DotsOCR weights: OK (../weights/DotsOCR)"
else
    echo "  WARNING: DotsOCR weights not found at ../weights/DotsOCR"
    echo "  Ensure the weights symlink is in place before processing documents."
fi

echo ""
echo "=== Pre-flight: Ollama check ==="
python3 -c "
import requests, sys
try:
    r = requests.get('http://localhost:11434/api/tags', timeout=5)
    models = [m['name'] for m in r.json().get('models', [])]
    print('Ollama models:', models)
    for m in ['qwen3-embedding:8b']:
        ok = any(m in name for name in models)
        print(f'  {m}: {\"OK\" if ok else \"MISSING — run: ollama pull \" + m}')
except Exception as e:
    print(f'ERROR: Ollama not reachable: {e}')
    sys.exit(1)
"
echo ""
echo "=== Starting ingest worker (sequential, DotsOCR/MPS + Ollama embed) ==="
echo "N_SEQ_WORKERS=${N_SEQ_WORKERS}"
echo "OCR: DotsOCR (${DOTS_OCR_WEIGHTS})  prompt=${DOTS_OCR_PROMPT_MODE}"
echo "Embed: Ollama ${OLLAMA_EMBED_MODEL} @ ${OLLAMA_BASE_URL}"
echo ""

python3 main.py

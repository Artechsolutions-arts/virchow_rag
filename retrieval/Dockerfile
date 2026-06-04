FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl ca-certificates \
    libgl1 libglib2.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# Core packages — exclude torch/torchvision (handled separately for ARM64 compat)
RUN grep -vE 'torch|torchvision' requirements.txt > req_core.txt && \
    pip install --no-cache-dir -r req_core.txt

# PyTorch CPU — torch==2.2.x+cpu only exists for x86_64;
# ARM64 (Apple Silicon / aarch64) needs 2.6.0+ from the CPU wheel index.
RUN pip install \
    "torch>=2.6.0,<3.0" \
    "torchvision>=0.21.0,<1.0" \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY . .

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "main.py"]


# Virchow RAG — Project Map

## How to run

```bash
cd /Users/macai/Desktop/virchow_rag
docker compose up -d          # starts all 6 services
docker compose down           # stop everything
docker compose up -d --build  # rebuild after code changes
```

## Directory layout

```
virchow_rag/
├── docker-compose.yml   ← single entry point — run from HERE
│
├── ingest/              ← document ingestion pipeline (port 8000)
│   │                      PDF → OCR (DotsOCR) → chunk → embed → pgvector
│   ├── Dockerfile
│   ├── main.py          ← RUN_TYPE=api starts FastAPI; RUN_TYPE=worker starts queue consumer
│   ├── src/             ← pipeline stages: ocr, chunking, embedding, storage
│   └── uploads/         ← temp upload landing area (bind-mounted into container)
│
├── retrieval/           ← RAG query service (port 8080)
│   │                      vector search + keyword search + LLM answer generation
│   ├── Dockerfile
│   ├── main.py
│   └── src/             ← config, routes, retrieval logic
│
├── web/                 ← Next.js frontend (port 3000)
│   ├── Dockerfile
│   └── src/             ← UI, API routes that proxy to retrieval:8080
│
├── dots_ocr/            ← shared OCR module (mounted read-only into ingest containers)
├── weights/             ← symlink → "Virchow backend/weights/DotsOCR" (8GB model, not copied)
│
├── Virchow backend/     ← OLD location — source of truth for: dots_ocr/, weights/, uploads history
│   └── RAG_complete_Backend_W 2/Rag_full_pipeline/   ← code now lives in ingest/
│
└── virchow frotend/     ← OLD location — source of truth for the Virchow upstream app
    ├── rag_pipeline/    ← code now lives in retrieval/ — docker-compose.yml here is DEPRECATED
    ├── web/             ← code now lives in web/
    └── backend/         ← upstream Virchow/Danswer app — NOT running, kept as reference
```

## Services in docker-compose.yml

| Service        | Port | What it does |
|----------------|------|--------------|
| redis          | 6379 | job state, SSE pub/sub |
| rabbitmq       | 5672 / 15672 | ingestion job queue (management UI on 15672) |
| ingest-api     | 8000 | receives upload requests, pushes to RabbitMQ |
| ingest-worker  | —    | consumes RabbitMQ jobs, runs OCR + embed pipeline |
| retrieval      | 8080 | answers queries via vector search + Ollama LLM |
| web            | 3000 | Next.js UI |

## External dependencies (not containerized)

| Dependency   | Address                | Notes |
|--------------|------------------------|-------|
| PostgreSQL   | 192.168.10.10:5433     | virchow_dev database |
| SeaweedFS    | 192.168.10.10:8889     | object storage for PDFs |
| Ollama LLM   | host.docker.internal:11434 | run `ollama serve` on host |

---

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health

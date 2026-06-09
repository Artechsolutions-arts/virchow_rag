# Virchow RAG

Document retrieval system for internal company knowledge. Upload PDFs, ask questions in plain English, get answers with source citations. Web UI plus native desktop apps for Mac and Windows.

**Live install:** 14,263 PDFs ingested, ~78K chunks indexed. Mac Studio M3 Ultra at `192.168.10.99` on the office LAN.

---

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [For employees: installing the desktop app](#for-employees-installing-the-desktop-app)
- [For admins: hosting the server](#for-admins-hosting-the-server)
  - [Prerequisites](#prerequisites)
  - [Install](#install)
  - [Start services](#start-services)
- [CI/CD: auto-deploy on push](#cicd-auto-deploy-on-push)
- [Releasing a new desktop app version](#releasing-a-new-desktop-app-version)
- [Project layout](#project-layout)
- [Configuration reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)

---

## What it does

1. **Ingest** — drop a PDF (or 100 of them). DotsOCR extracts text + layout, the chunker splits it into 600-token windows, and `qwen3-embedding:8b` embeds each chunk into a 4096-dim vector stored in pgvector.
2. **Retrieve** — when you ask a question, hybrid search (vector + keyword, alpha=0.6/beta=0.4) pulls the top 50 candidates, reranks to top 5.
3. **Answer** — `qwen2.5:latest` running on Ollama composes a grounded answer with inline source citations. Each citation links to the original PDF page.

Pipeline parameters are documented in [`PIPELINE_CONFIG.md`](./PIPELINE_CONFIG.md).

---

## Architecture

```
                           ┌─────────────────────────┐
        employee laptop ──▶│  Virchows Wiki app      │
   (Mac DMG or Win MSI)    │  (Pake/Tauri WebView)   │
                           └────────────┬────────────┘
                                        │ http://192.168.10.99:3000
                                        ▼
                           ┌─────────────────────────┐
                           │  Mac Studio (host)      │
                           │                         │
                           │  ┌───────────────────┐  │
                           │  │ Next.js web :3000 │  │
                           │  └─────────┬─────────┘  │
                           │            │            │
                           │  ┌─────────┴─────────┐  │
                           │  │ Retrieval :8080   │──┼──▶ Ollama :11434 (host)
                           │  └─────────┬─────────┘  │     - qwen2.5:latest (LLM)
                           │            │            │     - qwen3-embedding:8b
                           │  ┌─────────┴─────────┐  │
                           │  │ Ingest API :8000  │──┼──▶ RabbitMQ → Worker
                           │  └───────────────────┘  │     (DotsOCR + embed)
                           └────────────┬────────────┘
                                        │
                          ┌─────────────┴──────────────┐
                          ▼                            ▼
                ┌──────────────────┐         ┌──────────────────┐
                │ Postgres 5433    │         │ SeaweedFS 889    │
                │ pgvector(4096)   │         │ PDF object store │
                │ 192.168.10.10    │         │ 192.168.10.10    │
                └──────────────────┘         └──────────────────┘
```

| Container       | Port  | Purpose                                      |
|-----------------|-------|----------------------------------------------|
| `web`           | 3000  | Next.js UI, also proxies SeaweedFS for PDFs  |
| `retrieval`     | 8080  | Vector + keyword search, LLM answer assembly |
| `ingest-api`    | 8000  | Upload endpoint, pushes jobs to RabbitMQ     |
| `ingest-worker` | —     | Consumes queue, runs OCR + chunk + embed     |
| `rabbitmq`      | 5672  | Job queue (mgmt UI on 15672)                 |
| `redis`         | 6379  | Job state, SSE pub/sub                       |

External services (not containerized):

| Service     | Address                       | Notes                              |
|-------------|-------------------------------|------------------------------------|
| PostgreSQL  | `192.168.10.10:5433`          | DB: `virchow_dev`, pgvector ext    |
| SeaweedFS   | `192.168.10.10:889` (filer)   | bucket: `rag-docs`                 |
| Ollama      | `host.docker.internal:11434`  | runs natively on Mac Studio        |

---

## For employees: installing the desktop app

You don't need Docker, Python, or anything technical. Just download the installer.

### Mac
1. Go to [Releases](https://github.com/Artechsolutions-arts/virchow_rag/releases/latest).
2. Download `Virchows Wiki_<version>_universal.dmg`.
3. Open the DMG → drag **Virchows Wiki** to **Applications**.
4. First launch: right-click the app icon → **Open** → confirm (only needed once, because the build isn't notarized).
5. The app connects to the Mac Studio automatically. You must be on the office LAN or VPN.

### Windows
1. Download `Virchows Wiki_<version>_x64_en-US.msi` from the same Releases page.
2. Run the installer. SmartScreen may warn — click **More info** → **Run anyway**.
3. Launch from the Start menu. Same LAN requirement.

### Updates
The app checks for new versions every hour. When one is published, a banner appears at the top:

> *A new version of Virchows Wiki is available: v1.2.0 (you have v1.1.0)* `[Download]` `[Later]`

Click **Download** → it opens the Releases page in your browser → grab the new installer.

**Before installing a new version, uninstall the old one** (the version number doesn't auto-increment in the installer, so a same-version reinstall is treated as no-op):
- Mac: drag the old app from Applications to Trash.
- Windows: **Settings → Apps → Installed apps → Virchows Wiki → Uninstall**.

---

## For admins: hosting the server

### Prerequisites

Tested on macOS 14+ on a Mac Studio M3 Ultra (64GB RAM). Should work on any Linux box with similar specs.

- **Docker Desktop** (or Docker Engine + Compose v2)
- **Ollama** running natively on the host: `brew install ollama` then `ollama serve`
- **64GB+ RAM** — DotsOCR loads ~24GB; embedding model loads ~8GB; workers grow to 62GB before recycle
- **100GB+ disk** for the model weights and Docker images
- **PostgreSQL with pgvector** reachable on the LAN
- **SeaweedFS** reachable on the LAN
- **Rust** (only if you want to build the Mac DMG locally — CI builds it otherwise)

### Install

```bash
# 1. Clone
git clone https://github.com/Artechsolutions-arts/virchow_rag.git
cd virchow_rag

# 2. Pull the Ollama models (~17GB total)
ollama pull qwen3-embedding:8b
ollama pull qwen2.5:latest

# 3. Download the DotsOCR weights (~8GB) from HuggingFace
#    See https://huggingface.co/rednote-hilab/dots.ocr
mkdir -p weights
# Place the model at ./weights/DotsOCR/ (or symlink it from somewhere with space)

# 4. Verify external services are reachable
nc -zv 192.168.10.10 5433     # postgres
nc -zv 192.168.10.10 889      # seaweedfs filer
curl -fs http://localhost:11434/api/tags  # ollama
```

Edit `docker-compose.yml` to match your environment if any of these are different:
- `PG_HOST`, `PG_PORT`, `PG_PASSWORD`
- `SEAWEEDFS_*` URLs
- `INTERNAL_URL` for the web container

### Start services

```bash
docker compose up -d           # start everything
docker compose ps              # check health
docker compose logs -f web     # tail logs
docker compose down            # stop everything
docker compose up -d --build   # rebuild after code changes (CI does this for you)
```

First-time startup takes 5-10 minutes for the ingest-worker to load DotsOCR.

**Mac-native ingest worker (much faster):** the Dockerized worker uses CPU only. On Apple Silicon you can run the worker natively using MPS for ~5-10x throughput:

```bash
docker compose stop ingest-worker
cd ingest && bash run_native.sh
```

You can run both simultaneously — they share the same RabbitMQ queue.

---

## CI/CD: auto-deploy on push

When you push to `main`, the `deploy.yml` workflow picks up the change, figures out which services were touched (`web/`, `retrieval/`, `ingest/`, or `dots_ocr/`), and rebuilds only those containers. Users see new features on the next page refresh inside the app — no installer update needed.

This requires a **self-hosted GitHub Actions runner** on the Mac Studio (one-time setup, ~3 minutes).

### Install the runner

1. Open **https://github.com/Artechsolutions-arts/virchow_rag/settings/actions/runners/new** and pick **macOS / ARM64**. GitHub shows you the exact commands with a fresh token.

2. On the Mac Studio, run roughly:

   ```bash
   mkdir -p ~/actions-runner && cd ~/actions-runner
   curl -o runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.X.X/actions-runner-osx-arm64-2.X.X.tar.gz
   tar xzf runner.tar.gz
   ./config.sh --url https://github.com/Artechsolutions-arts/virchow_rag --token <TOKEN-FROM-GITHUB>
   ```

   When prompted:
   - Runner group: press Enter
   - Runner name: `mac-studio`
   - Labels: press Enter (gets `self-hosted` + `macOS` automatically)
   - Work folder: press Enter

3. Install as a launchd service so it survives reboots:

   ```bash
   ./svc.sh install
   ./svc.sh start
   ```

4. Verify on the Runners page — `mac-studio` should show **Idle** with a green dot.

### How the deploy workflow works

Push to `main` →
1. Runner pulls the latest commit into `/Users/macai/Desktop/virchow_rag` and resets to it.
2. Diffs the new commit against the previous to determine which services changed.
3. Runs `docker compose up -d --build <service>` only for changed services.
4. Health-checks all three services. Fails the run if anything is down.

Skip auto-deploy by pushing with no relevant files changed (workflow has `paths:` filter).

---

## Releasing a new desktop app version

You only need to rebuild the desktop app when the **Pake wrapper itself** changes (icon, name, window size, `inject.js`, target URL). Pure web/API changes are picked up automatically because the desktop app is just a WebView.

### Cut a release

```bash
git tag v1.2.0
git push --tags
```

That triggers `release-desktop.yml`:
1. **macos-latest** runner builds the universal DMG (~3 min).
2. **windows-latest** runner builds the MSI (~12 min).
3. **ubuntu-latest** runner downloads both artifacts and creates a GitHub Release at `v1.2.0` with auto-generated release notes and both installers attached.

The version tag is also injected into `inject.js` so the running app knows what version it is and the update-checker can do a meaningful comparison.

### Trigger manually without tagging

Actions → **Build & Release Virchows Wiki Desktop** → **Run workflow** → enter a version string. Artifacts will be available on the workflow run page but won't create a GitHub Release (Release only fires for tag pushes).

### Build locally (Mac DMG only)

```bash
source ~/.cargo/env
npx pake-cli http://192.168.10.99:3000 \
  --name "Virchows Wiki" \
  --width 1440 --height 900 \
  --hide-title-bar --enable-find \
  --inject ./inject.js --icon ./virchow_icon.png \
  --multi-arch \
  --activation-shortcut "CmdOrControl+Shift+V"
```

Output: `Virchows Wiki.dmg` at the project root.

---

## Project layout

```
virchow_rag/
├── docker-compose.yml           single entry point — `docker compose up -d`
├── inject.js                    JS injected into the desktop WebView (link routing + update banner)
├── virchow_icon.png             512×512 app icon used by Mac and Windows
│
├── ingest/                      PDF → OCR → chunk → embed → pgvector
│   ├── Dockerfile
│   ├── main.py                  RUN_TYPE=api | worker
│   └── src/
│
├── retrieval/                   query → hybrid search → LLM answer
│   ├── Dockerfile
│   └── src/
│
├── web/                         Next.js frontend
│   ├── Dockerfile
│   └── src/
│
├── dots_ocr/                    DotsOCR module (mounted into ingest containers)
├── weights/                     8GB model weights — gitignored, mount only
│
└── .github/workflows/
    ├── deploy.yml               self-hosted runner: rebuild changed containers on push
    └── release-desktop.yml      build Mac DMG + Windows MSI on tag → GitHub Release
```

---

## Configuration reference

All runtime config goes through `docker-compose.yml` env blocks. The most commonly tuned settings:

| Variable                | Default                          | What it controls                                  |
|-------------------------|----------------------------------|---------------------------------------------------|
| `PG_HOST` / `PG_PORT`   | `192.168.10.10` / `5433`         | PostgreSQL location                               |
| `PG_PASSWORD`           | (required)                       | DB password — set per deploy                      |
| `SEAWEEDFS_FILER_URL`   | `http://192.168.10.10:889`       | SeaweedFS filer                                   |
| `EMBEDDING_MODEL`       | `qwen3-embedding:8b`             | Must match Ollama-pulled tag                      |
| `EMBEDDING_DIM`         | `4096`                           | Must match pgvector column type                   |
| `LLM_MODEL`             | `qwen2.5:14b-instruct`           | Answer-generation model                           |
| `SIM_THRESHOLD`         | `0.38`                           | Min cosine sim to include a chunk                 |
| `TOP_K`                 | `8`                              | Chunks passed to the LLM                          |
| `MAX_TOKENS`            | `4096`                           | LLM output cap                                    |
| `JWT_SECRET`            | (required in prod)               | Auth token signing                                |
| `UPLOAD_WORKERS`        | `2`                              | Parallel upload handlers                          |
| `N_SEQ_WORKERS`         | `2`                              | Sequential workers inside ingest-worker           |

Full pipeline tuning lives in [`PIPELINE_CONFIG.md`](./PIPELINE_CONFIG.md).

---

## Troubleshooting

**Desktop app shows blank screen**
Check that you're on the office LAN. The app hardcodes `http://192.168.10.99:3000`. If you're remote, set up Tailscale or a VPN to the office network. Open a normal browser to `http://192.168.10.99:3000` to verify the server is reachable.

**Source PDF links don't open**
Make sure you're on v1.0.0 or later of the desktop app — older builds tried to open SeaweedFS URLs directly, which Tauri blocks on Windows. The fix routes everything through the Next.js proxy at `/api/chat/file/...`.

**Ingest worker crashes with OOM**
The supervisor SIGKILLs the worker when RSS hits 62GB and restarts it (~10-15 min downtime). This is expected; see `supervisor.py`. If you hit it constantly, reduce `N_SEQ_WORKERS` or run a single worker.

**Update banner doesn't show**
The banner is only injected into CI-built installers (the version placeholder gets replaced at build time). Locally-built DMGs have no version embedded, so the updater is a no-op.

**Self-hosted runner shows "Offline"**
```bash
cd ~/actions-runner
./svc.sh status     # is it running?
./svc.sh stop && ./svc.sh start   # restart
tail -f _diag/Runner_*.log         # check logs
```

**`docker compose up` fails with "trust_remote_code" / "Read-only file system"**
The DotsOCR weights are mounted read-only but HuggingFace tries to cache modules there. Make sure `HF_HOME: /tmp/hf_cache` and `TRANSFORMERS_CACHE: /tmp/hf_cache` are set on `ingest-worker` (already in `docker-compose.yml`).

**Pake build fails: "Target x86_64-apple-darwin is not installed"**
```bash
rustup target add x86_64-apple-darwin
rustup target add aarch64-apple-darwin
```

---

Built by Artech Solutions. PRs welcome.

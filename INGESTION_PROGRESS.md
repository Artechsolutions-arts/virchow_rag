# Virchow RAG — Ingestion Progress Report

**Generated:** 2026-05-11 14:06 IST
**Corpus:** 11,043 total docs | **Completed:** 9,943 | **Remaining:** 1,100 | **Progress:** 90.0%

---

## Overall Summary

| Metric | Value |
|--------|-------|
| Pipeline start | 2026-04-24 |
| Days running | 18 days |
| Workers | 3 parallel (restart on 62 GB RSS) |
| Total completed | 9,943 / 11,043 |
| Completion % | 90.0% |
| Total pages processed | 47,827 pages |
| Avg pages per file | 4.8 pages |
| Avg processing time per file | 27.3 min |

---

## Daywise Breakdown

| Day | Workers | Files Completed | Cumulative | Avg Pages | Avg Time/File | Min Time | Max Time | Total Pages |
|-----|:-------:|----------------:|-----------:|----------:|--------------:|---------:|---------:|------------:|
| 2026-04-24 * | 3 | 196 | 196 | 1.8 | 10.8 min | 3.6 min | 98.5 min | 343 |
| 2026-04-25 | 3 | 586 | 782 | 1.2 | 9.1 min | 0.8 min | 57.6 min | 710 |
| 2026-04-26 | 3 | 1,210 | 1,992 | 2.1 | 18.7 min | 2.9 min | 187.2 min | 2,509 |
| 2026-04-27 | 3 | 1,124 | 3,116 | 2.4 | 15.5 min | 4.1 min | 154.5 min | 2,746 |
| 2026-04-28 | 3 | 629 | 3,745 | 4.9 | 27.0 min | 11.5 min | 163.0 min | 3,059 |
| 2026-04-29 | 3 | 535 | 4,280 | 5.6 | 31.4 min | 18.2 min | 157.4 min | 2,998 |
| 2026-04-30 | 3 | 503 | 4,783 | 5.7 | 33.1 min | 12.1 min | 134.3 min | 2,864 |
| 2026-05-01 | 3 | 553 | 5,336 | 5.8 | 31.0 min | 10.9 min | 155.1 min | 3,213 |
| 2026-05-02 | 3 | 518 | 5,854 | 6.0 | 32.5 min | 19.4 min | 61.9 min | 3,113 |
| 2026-05-03 | 3 | 466 | 6,320 | 6.3 | 36.3 min | 22.0 min | 65.9 min | 2,920 |
| 2026-05-04 | 3 | 651 | 6,971 | 4.1 | 24.0 min | 3.0 min | 125.2 min | 2,651 |
| 2026-05-05 | 3 | 565 | 7,536 | 5.0 | 24.8 min | 9.6 min | 104.8 min | 2,834 |
| 2026-05-06 | 3 | 534 | 8,070 | 6.2 | 31.5 min | 18.1 min | 61.8 min | 3,326 |
| 2026-05-07 | 3 | 435 | 8,505 | 7.1 | 37.2 min | 22.1 min | 65.3 min | 3,080 |
| 2026-05-08 | 3 | 414 | 8,919 | 7.4 | 39.2 min | 23.6 min | 71.3 min | 3,043 |
| 2026-05-09 | 3 | 422 | 9,341 | 7.8 | 39.4 min | 23.3 min | 95.4 min | 3,271 |
| 2026-05-10 | 3 | 395 | 9,736 | 8.2 | 41.8 min | 24.7 min | 123.4 min | 3,234 |
| 2026-05-11 *(live)* | 3 | 207 | **9,943** | 8.3 | 45.3 min | 30.3 min | 116.5 min | 1,727 |

> \* Apr 24: cold-start day — workers loading models from scratch.

---

## Throughput Trend

| Period | Avg Pages/File | Avg Time/File | Files/Day |
|--------|---------------:|--------------:|----------:|
| Apr 24–27 (early) | 1.9 | 13.5 min | 780 |
| Apr 28–May 03 (mid) | 5.7 | 31.9 min | 534 |
| May 04–11 (late/now) | 6.7 | 36.0 min | 452 |

Throughput decline = larger docs, not fewer workers. Same 3 workers throughout.

---

## Worker Kill Cycle Protocol

| Step | Action |
|------|--------|
| Trigger | Any worker RSS ≥ 62 GB |
| 1 | SIGKILL the worker |
| 2 | Reset `processing` → `pending` in DB |
| 3 | Start fresh replacement worker (~10 min to load model) |
| 4 | Re-queue all pending docs to RabbitMQ |

**17+ kill cycles** completed. Each cycle: ~10–15 min downtime for 1 worker; other 2 continue uninterrupted.

**Current RSS (14:06 IST):**
- PID 5486: 60.5 GB — next kill approaching
- PID 38965: 58.4 GB
- PID 90131: 55.2 GB

---

## Adaptive Check Intervals

| RSS Level | Check Interval |
|-----------|---------------|
| < 50 GB | 15 min |
| 50–53 GB | 10 min |
| 53–61.5 GB | 5 min ← current |
| 61.5–62 GB | 2 min |
| ≥ 62 GB | Kill immediately |

---

## Estimated Completion

| Metric | Value |
|--------|-------|
| Remaining files | ~1,100 |
| Current rate | ~30 files/hr |
| Est. hours remaining | ~36–38 hrs |
| Next kill cycle (PID 5486) | ~25 min (at 60.5 GB) |

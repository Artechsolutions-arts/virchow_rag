import uvicorn
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config import cfg
from src.database.postgres_db import get_pg_pool, create_schema
from src.database.migrations import run_all_migrations
from src.services.rag_pipeline import RetrievalService
from src.api.routes import create_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("virchow")


def bootstrap():
    logger.info("Starting Virchow Retrieval API…")

    pool = get_pg_pool()
    conn = pool.getconn()
    try:
        try:
            create_schema(conn)
        except Exception as e:
            logger.warning(f"Schema creation skipped (tables may already exist): {e}")
            conn.rollback()
        run_all_migrations(conn)
    finally:
        pool.putconn(conn)

    svc = RetrievalService(pool)

    app = FastAPI(title="Virchow — RAG Retrieval API", version="2.0")

    # B-C2: CORS — never use wildcard with credentials.
    # Origins are configurable via CORS_ORIGINS env var (comma-separated).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )

    app.include_router(create_router(svc))

    # B-H6: Close connection pool on shutdown to avoid leaked connections.
    @app.on_event("shutdown")
    def shutdown():
        logger.info("Closing PostgreSQL connection pool…")
        pool.closeall()

    return app


app = bootstrap()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

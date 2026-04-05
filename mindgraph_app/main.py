"""FastAPI app — serves CodeGraph API + legacy MindGraph API + static frontend."""

import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from mindgraph_app.api import router, process_router, rate_router
from mindgraph_app.codegraph_api import codegraph_router
from jobpulse.health_api import health_router
from jobpulse.analytics_api import analytics_router
from jobpulse.job_api import job_api_router
from shared.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="CodeGraph", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# New CodeGraph endpoints (primary)
app.include_router(codegraph_router)

# Legacy MindGraph endpoints (still used by jobpulse)
app.include_router(router)
app.include_router(process_router)
app.include_router(rate_router)

# JobPulse dashboards
app.include_router(health_router)
app.include_router(analytics_router)
app.include_router(job_api_router)

# Chrome extension HTTP API
app.include_router(job_api_router)

# Serve static frontend
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def main():
    logger.info("CodeGraph starting at http://localhost:8000")
    logger.info("  CodeGraph API: http://localhost:8000/api/codegraph/graph")
    logger.info("  Risk Report:   http://localhost:8000/api/codegraph/risk-report")
    logger.info("  Patterns:      http://localhost:8000/api/codegraph/patterns")
    logger.info("  Swagger UI:    http://localhost:8000/docs")
    uvicorn.run("mindgraph_app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()

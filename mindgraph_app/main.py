"""FastAPI app — serves API + static frontend."""

import uvicorn
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from mindgraph_app.api import router, process_router, rate_router
from jobpulse.health_api import health_router
from jobpulse.analytics_api import analytics_router
from shared.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="MindGraph", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(process_router)
app.include_router(rate_router)
app.include_router(health_router)
app.include_router(analytics_router)

# Serve static frontend
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def main():
    logger.info("MindGraph starting at http://localhost:8000")
    uvicorn.run("mindgraph_app.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()

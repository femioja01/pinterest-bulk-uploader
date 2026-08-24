"""Pinterest Bulk Pin Uploader — FastAPI Application Entry Point."""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import DATA_DIR, ACCOUNTS_DIR
from src.models.database import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pinterest-uploader")

# Global scheduler reference (used by schedule routes to update live)
scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    global scheduler

    logger.info("Starting Pinterest Bulk Pin Uploader...")

    # Ensure data directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize database
    init_db()
    logger.info(f"Database initialized at {DATA_DIR / 'app.db'}")

    # Start scheduler
    try:
        from src.services.scheduler import create_scheduler, start_scheduler
        scheduler = create_scheduler()
        start_scheduler(scheduler)
        logger.info("Scheduler started")
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")

    # Check if RUN_NOW is set (for testing)
    if os.getenv("RUN_NOW", "").lower() == "true":
        logger.info("RUN_NOW is set — triggering immediate upload cycle")
        try:
            from src.services.scheduler import run_upload_cycle
            import threading
            thread = threading.Thread(target=run_upload_cycle, daemon=True)
            thread.start()
        except Exception as e:
            logger.error(f"Immediate run failed: {e}")

    yield

    # Shutdown
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    logger.info("Application shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Pinterest Bulk Pin Uploader",
    description="Automate CSV uploads to Pinterest's bulk create pins page",
    version="1.0.0",
    lifespan=lifespan,
)

# Static files
app.mount("/static", StaticFiles(directory="src/static"), name="static")

# Register routes
from src.routes.dashboard import router as dashboard_router
from src.routes.accounts import router as accounts_router
from src.routes.upload import router as upload_router
from src.routes.history import router as history_router
from src.routes.schedule import router as schedule_router
from src.routes.generator import router as generator_router
from src.routes.settings import router as settings_router

app.include_router(dashboard_router)
app.include_router(accounts_router)
app.include_router(upload_router)
app.include_router(history_router)
app.include_router(schedule_router)
app.include_router(generator_router)
app.include_router(settings_router)


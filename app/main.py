"""FastAPI application factory, routers, lifespan, and static file mounting."""

import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.queue import start_worker, stop_worker
from app.routes.api import router as api_router
from app.routes.auth import router as auth_router
from app.routes.views import router as views_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("gmaps_scraper.web")

BASE_DIR = Path(__file__).resolve().parent

# Basic in-memory IP rate limiter
_request_counts: dict[str, list[float]] = defaultdict(list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan manager for background worker initialization and clean shutdown."""
    logger.info("Initializing Google Maps Lead Scraper Web Application...")
    logger.info(f"Persistent Output Directory: {config.OUTPUT_DIR}")
    logger.info(f"Concurrency Limit: {config.CONCURRENCY_LIMIT}")
    logger.info(f"Headless Mode: {config.HEADLESS}")

    # Start background worker loop (uses Redis if configured, otherwise local async queue)
    start_worker()

    yield

    logger.info("Shutting down background workers...")
    await stop_worker()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="Google Maps Lead Scraper",
    description="Browser-based lead generation and export application deployable on Railway",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_and_security_headers_middleware(request: Request, call_next):
    """Apply basic rate limiting and security headers."""
    client_ip = request.client.host if request.client else "127.0.0.1"

    if config.RATE_LIMIT_ENABLED and request.url.path.startswith("/api/"):
        now = time.time()
        window_start = now - config.RATE_LIMIT_WINDOW_SECONDS
        recent = [t for t in _request_counts[client_ip] if t > window_start]
        recent.append(now)
        _request_counts[client_ip] = recent

        if len(recent) > config.RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many requests. Please slow down."},
            )

    response: Response = await call_next(request)

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    return response


# Mount Static Files
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Include Routers
app.include_router(auth_router)
app.include_router(views_router)
app.include_router(api_router)


# Root Health Check (Railway requirement)
@app.get("/health", tags=["Health"])
async def root_health():
    return {
        "status": "healthy",
        "app": "google-maps-lead-scraper",
        "version": "2.0.0",
        "storage": str(config.OUTPUT_DIR),
    }

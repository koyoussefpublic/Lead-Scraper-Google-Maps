"""Configuration settings for web application and worker service."""

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    try:
        return int(val) if val not in (None, "") else default
    except ValueError:
        return default


BASE_DIR = Path(__file__).resolve().parent.parent

# Output directory: default to /app/output in Docker/Railway, or ./output locally
_default_output = "/app/output" if Path("/app").exists() else str(BASE_DIR / "output")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", _default_output)).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Web server settings
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8000)

# Security & Authentication
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin123")
APP_PASSWORD_HASH = os.environ.get("APP_PASSWORD_HASH", None)
SECRET_KEY = os.environ.get("SECRET_KEY", "lead-scraper-railway-secret-key-change-in-prod")
SESSION_COOKIE_NAME = "lead_scraper_session"
SESSION_MAX_AGE = _env_int("SESSION_MAX_AGE", 86400 * 7)  # 7 days

# Worker and Queue configuration
REDIS_URL = os.environ.get("REDIS_URL", None)
REDIS_QUEUE_KEY = os.environ.get("REDIS_QUEUE_KEY", "gmaps_scraper:jobs_queue")
REDIS_STATE_KEY_PREFIX = os.environ.get("REDIS_STATE_KEY_PREFIX", "gmaps_scraper:job:")
CONCURRENCY_LIMIT = _env_int("CONCURRENCY_LIMIT", 1)

# Default to headless for server/worker runs
HEADLESS = _env_bool("HEADLESS", True)

# Rate limiting
RATE_LIMIT_ENABLED = _env_bool("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_MAX_REQUESTS = _env_int("RATE_LIMIT_MAX_REQUESTS", 60)
RATE_LIMIT_WINDOW_SECONDS = _env_int("RATE_LIMIT_WINDOW_SECONDS", 60)

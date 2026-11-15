"""Web server entry point for Railway and local browser use."""

import os
import uvicorn
from app import config

if __name__ == "__main__":
    port = int(os.environ.get("PORT", config.PORT))
    host = os.environ.get("HOST", config.HOST)
    print(f"Starting Lead Scraper Web Application on http://{host}:{port}")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )

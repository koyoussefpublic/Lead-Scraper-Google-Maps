# Official Microsoft Playwright image: Python + baseline OS packages for Chromium
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HEADLESS=1 \
    OUTPUT_DIR=/app/output \
    PORT=8000

# Install application dependencies and Playwright Chromium with Linux dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium --with-deps

# Create persistent output directory
RUN mkdir -p /app/output

# Copy project source files
COPY . .

# Expose standard Railway port
EXPOSE 8000

# Health check probe against the web application
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8000)}/health')" || exit 1

# Default command: launch FastAPI web server
CMD ["python", "web.py"]

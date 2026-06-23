# Meridian backend image (Phase 8 — container hardening).
# Runs the FastAPI app as a NON-ROOT user. Postgres/Redis/etc. stay on the
# internal compose network; only the app's 8000 is published.
FROM python:3.13-slim

# Faster, quieter, no .pyc clutter; unbuffered logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching). Only the files needed to
# resolve + install the package are copied — NOT the whole tree (see .dockerignore).
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir -e .

# Create an unprivileged user and own the app dir + a home for caches
# (sentence-transformers/HF write to ~/.cache at runtime).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# --timeout-keep-alive bounds how long a slow client can hold a connection
# (Phase 8 request/timeout limits). Host/port bind to all interfaces inside the
# container only; publishing is controlled by docker-compose.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive ${SERVER_KEEPALIVE_TIMEOUT:-15}"]

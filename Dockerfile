# syntax=docker/dockerfile:1.7

# 1. Build the React frontend.
FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npm run build

# 2. Install Python 3.15 and the backend with uv.
FROM debian:trixie-slim AS backend
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=automatic
# A compiler for dependencies without prebuilt Python 3.15 wheels; it stays in this stage.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY .python-version pyproject.toml uv.lock README.md ./
RUN uv python install && uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# 3. Runtime: the API serves the built frontend from the same origin.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app app \
    && mkdir -p /data/storage && chown app /data/storage
COPY --from=backend /opt/python /opt/python
COPY --from=backend /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend /frontend/dist ./frontend/dist
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    STORAGE_DIR=/data/storage
USER app
EXPOSE 8000
VOLUME ["/data/storage"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["ceo-backend", "serve", "--host", "0.0.0.0", "--port", "8000"]

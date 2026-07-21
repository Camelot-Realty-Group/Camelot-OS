# =============================================================================
# Camelot OS — Orchestrator Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim AS base

# curl needed for docker-compose healthchecks
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS orchestrator
COPY orchestrator/ ./orchestrator/
RUN mkdir -p /app/logs /app/data

ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/orchestrator

EXPOSE 8000
CMD ["python", "orchestrator/api_server.py"]

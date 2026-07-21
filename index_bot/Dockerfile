# =============================================================================
# Camelot OS — Index Bot Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY index_bot/ ./index_bot/
RUN mkdir -p /app/logs /app/data /app/cache

# PYTHONPATH=/app so `from index_bot.x import ...` package imports resolve
ENV HOST=0.0.0.0 \
    PORT=8002 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8002
CMD ["python", "index_bot/api_server.py"]

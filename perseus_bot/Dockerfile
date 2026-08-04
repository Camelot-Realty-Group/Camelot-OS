# =============================================================================
# Camelot OS — Perseus Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY perseus_bot/ ./perseus_bot/
COPY utils/ ./utils/
RUN mkdir -p /app/logs /app/output/perseus

# PYTHONPATH=/app so `from perseus_bot.x import ...` package imports resolve
ENV HOST=0.0.0.0 \
    PORT=8006 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8006
CMD ["python", "perseus_bot/main.py", "--serve", "--port", "8006"]

# =============================================================================
# Camelot OS — Front Desk Bot Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY frontdesk_bot/ ./frontdesk_bot/
RUN mkdir -p /app/logs /app/data

# PYTHONPATH=/app so `from frontdesk_bot.x import ...` package imports resolve
ENV HOST=0.0.0.0 \
    PORT=8001 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8001
CMD ["python", "frontdesk_bot/main.py", "--serve", "--port", "8001"]

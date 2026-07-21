# =============================================================================
# Camelot OS — Concierge Bot Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# library/ (63 branded docs) + masters/ (merge-tag docx) ship in the image
COPY concierge_bot/ ./concierge_bot/
RUN mkdir -p /app/logs /app/data

ENV HOST=0.0.0.0 \
    PORT=8004 \
    PYTHONUNBUFFERED=1

EXPOSE 8004
CMD ["python", "concierge_bot/main.py", "--serve", "--port", "8004"]

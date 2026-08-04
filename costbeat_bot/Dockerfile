# =============================================================================
# Camelot OS — CostBeat Bot Dockerfile
# Build context: repo root (see orchestrator/docker-compose.yml: context: ..)
# =============================================================================
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY costbeat_bot/ ./costbeat_bot/
COPY utils/ ./utils/
RUN mkdir -p /app/logs /app/output/costbeat

# PYTHONPATH=/app so `from utils.audit_log import ...` resolves
ENV HOST=0.0.0.0 \
    PORT=8005 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8005
CMD ["python", "costbeat_bot/main.py", "--serve", "--port", "8005"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --system appgroup && useradd --system --gid appgroup --home-dir /app appuser

COPY --chown=appuser:appgroup . .

EXPOSE 8501
EXPOSE 8503

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl --fail http://localhost:8503/_stcore/health || exit 1

CMD ["streamlit", "run", "Home.py", "--server.port=8503", "--server.address=0.0.0.0", "--server.headless=true"]

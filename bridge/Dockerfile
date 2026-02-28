# AI Bridge — token tracking proxy
# Lightweight wrapper between consumers and CLIProxyAPI
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV UPSTREAM_URL=http://cliproxyapi:8317
ENV BRIDGE_DB_PATH=/data/tokens.db

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8318/health')"

EXPOSE 8318

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8318", "--log-level", "info"]

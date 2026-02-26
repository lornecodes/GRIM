# ── GRIM Container ──────────────────────────────────────────
# Multi-stage build: slim Python + GRIM core + chat UI
#
# Build:
#   docker build -t grim .
#
# Run:
#   docker run -p 8080:8080 \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -v /path/to/kronos-vault:/vault \
#     grim

FROM python:3.11-slim AS base

# System deps for MCP stdio transport
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependencies ────────────────────────────────────────────
COPY pyproject.toml ./
COPY mcp/kronos/pyproject.toml mcp/kronos/pyproject.toml
COPY mcp/kronos/src/ mcp/kronos/src/

# Install GRIM + kronos-mcp + server deps
RUN pip install --no-cache-dir . && \
    pip install --no-cache-dir ./mcp/kronos && \
    pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.34" "websockets>=14.0"

# ── Application ─────────────────────────────────────────────
COPY core/ core/
COPY server/ server/
COPY config/ config/
COPY identity/ identity/
COPY skills/ skills/

# Default env
ENV GRIM_ENV=production
ENV KRONOS_VAULT_PATH=/vault
ENV KRONOS_SKILLS_PATH=/app/skills

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

EXPOSE 8080

# ── Entrypoint ──────────────────────────────────────────────
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]

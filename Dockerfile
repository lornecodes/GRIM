# ── GRIM Container ──────────────────────────────────────────
# Multi-stage build: Node.js (UI) + Python (backend)
#
# Build:
#   docker build -t grim .
#
# Run:
#   docker run -p 8080:8080 \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -v /path/to/kronos-vault:/vault \
#     grim

# ── Stage 1: Build Next.js UI ─────────────────────────────
FROM node:20-slim AS ui-build
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ ./
# Clear localhost env so UI uses relative/same-origin URLs (works from any host)
ENV NEXT_PUBLIC_GRIM_API=""
ENV NEXT_PUBLIC_BRIDGE_URL=""
RUN npm run build

# ── Stage 2: Python backend ───────────────────────────────
FROM python:3.11-slim AS base

# System deps for MCP stdio transport
RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependencies ────────────────────────────────────────────
RUN pip install --no-cache-dir --upgrade pip setuptools

COPY pyproject.toml ./
COPY mcp/kronos/pyproject.toml mcp/kronos/README.md mcp/kronos/
COPY mcp/kronos/src/ mcp/kronos/src/

# Install GRIM + kronos-mcp + server deps + semantic search + test deps
# PyTorch CPU-only via --extra-index-url (falls back to PyPI if CPU wheel unavailable)
RUN pip install --no-cache-dir ".[server,cache,pool,dev]" && \
    pip install --no-cache-dir --retries 10 --timeout 300 \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    "./mcp/kronos[cache,semantic]"

# ── Application ─────────────────────────────────────────────
COPY core/ core/
COPY server/ server/
COPY clients/ clients/
COPY config/ config/
COPY identity/ identity/
COPY skills/ skills/
COPY eval/ eval/
COPY tests/ tests/

# ── Next.js static build ──────────────────────────────────
COPY --from=ui-build /ui/out/ ui/out/

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

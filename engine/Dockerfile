# IronClaw Engine — multi-stage Rust build
# Produces a minimal Debian image with only the ironclaw binary.

# ── Build stage ──
FROM rust:1.93-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy manifests first for dependency caching
COPY Cargo.toml Cargo.lock ./

# Create dummy src to build deps only
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release 2>/dev/null || true

# Now copy actual source and rebuild
COPY src/ src/
RUN touch src/main.rs && cargo build --release

# ── Runtime stage ──
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /build/target/release/ironclaw /usr/local/bin/ironclaw
COPY config/ /etc/ironclaw/

# Non-root user
RUN useradd -r -s /usr/sbin/nologin ironclaw

# Create data directories (owned by ironclaw user)
RUN mkdir -p /app/logs /app/data && chown -R ironclaw:ironclaw /app/logs /app/data

USER ironclaw

EXPOSE 3100

CMD ["ironclaw", "--config", "/etc/ironclaw/grim.toml", "ui"]

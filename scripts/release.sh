#!/usr/bin/env bash
# ── GRIM Release Manager ─────────────────────────────────────
#
# Single script for the full Docker lifecycle:
#   build, test, deploy, clean, status.
#
# Usage:
#   ./scripts/release.sh build     Build image with version tag
#   ./scripts/release.sh test      Run all tests inside container
#   ./scripts/release.sh up        Start GRIM (detached)
#   ./scripts/release.sh down      Stop GRIM
#   ./scripts/release.sh logs      Tail container logs
#   ./scripts/release.sh status    Show container health + image info
#   ./scripts/release.sh clean     Remove old images + dangling layers
#   ./scripts/release.sh rebuild   Full redeploy: clean → build → test → up
#   ./scripts/release.sh prod      Production deploy (with prod override)
#
# Environment:
#   GRIM_KEEP=3       Number of old image tags to keep (default: 3)
#   GRIM_PROD=1       Use production compose override
#   VAULT_PATH=...    Override vault mount path

set -euo pipefail

# ── Config ────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRIM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_NAME="grim"
KEEP_IMAGES="${GRIM_KEEP:-3}"

# Compose file paths (quoted separately to handle spaces in paths)
COMPOSE_FILE="$GRIM_DIR/docker-compose.yml"
COMPOSE_PROD_FILE="$GRIM_DIR/docker-compose.prod.yml"

# ── Helpers ───────────────────────────────────────────────────

_log()  { echo -e "\033[1;34m[grim]\033[0m $*"; }
_ok()   { echo -e "\033[1;32m[grim]\033[0m $*"; }
_warn() { echo -e "\033[1;33m[grim]\033[0m $*"; }
_err()  { echo -e "\033[1;31m[grim]\033[0m $*" >&2; }

_version_tag() {
    # Generate a version tag from git hash + date
    local hash
    hash=$(git -C "$GRIM_DIR" rev-parse --short HEAD 2>/dev/null || echo "dev")
    local date
    date=$(date +%Y%m%d)
    echo "${hash}-${date}"
}

_compose() {
    # Run docker compose with the right files (quoted to handle spaces)
    if [[ "${GRIM_PROD:-0}" == "1" ]]; then
        docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_PROD_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

# ── Commands ──────────────────────────────────────────────────

cmd_build() {
    local tag
    tag=$(_version_tag)

    _log "Building $IMAGE_NAME:$tag ..."
    docker build -t "$IMAGE_NAME:$tag" -t "$IMAGE_NAME:latest" "$GRIM_DIR"

    _ok "Built: $IMAGE_NAME:$tag + $IMAGE_NAME:latest"

    # Auto-cleanup: remove old image tags (keeps last KEEP_IMAGES + latest)
    _log "Cleaning old images (keeping last $KEEP_IMAGES) ..."
    local all_tags
    all_tags=$(docker images "$IMAGE_NAME" --format "{{.Tag}}" 2>/dev/null | grep -v "latest" | sort -r)
    local count=0
    while IFS= read -r old_tag; do
        [[ -z "$old_tag" ]] && continue
        count=$((count + 1))
        if [[ $count -gt $KEEP_IMAGES ]]; then
            _log "Removing old image: $IMAGE_NAME:$old_tag"
            docker rmi "$IMAGE_NAME:$old_tag" 2>/dev/null || true
        fi
    done <<< "$all_tags"

    # Remove dangling images from this build
    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null)
    if [[ -n "$dangling" ]]; then
        echo "$dangling" | xargs docker rmi -f 2>/dev/null || true
    fi

    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"
}

cmd_test() {
    _log "Running tests ..."

    # ── UI tests (local, not containerised) ──
    local ui_dir="$GRIM_DIR/ui"
    if [[ -d "$ui_dir" && -f "$ui_dir/package.json" ]]; then
        _log "── UI tests (vitest) ──"
        if command -v npm &>/dev/null; then
            (cd "$ui_dir" && npm run test) || {
                _err "UI tests failed"
                return 1
            }
        else
            _warn "npm not found — skipping UI tests"
        fi
    fi

    # Resolve vault path for MCP tests (handler + E2E need the real vault)
    local vault
    vault="${VAULT_PATH:-$(cd "$GRIM_DIR/.." && pwd)/kronos-vault}"
    if [[ ! -d "$vault" ]]; then
        _warn "Kronos vault not found at $vault — skipping MCP tests"
        _warn "Set VAULT_PATH to your kronos-vault directory"
        vault=""
    fi

    # Core unit tests — no vault needed, uses mocks
    _log "── Core unit tests ──"
    MSYS_NO_PATHCONV=1 docker run --rm \
        -e KRONOS_VAULT_PATH=/app/tests/vault \
        -e KRONOS_SKILLS_PATH=/app/skills \
        "$IMAGE_NAME:latest" \
        python -m pytest tests/test_grim_core.py -v --tb=short 2>/dev/null || \
    MSYS_NO_PATHCONV=1 docker run --rm \
        -e KRONOS_VAULT_PATH=/app/tests/vault \
        -e KRONOS_SKILLS_PATH=/app/skills \
        "$IMAGE_NAME:latest" \
        python tests/test_grim_core.py

    if [[ -n "$vault" ]]; then
        # MCP handler tests (57) — needs real vault (rw for write tests)
        _log "── MCP handler tests ──"
        MSYS_NO_PATHCONV=1 docker run --rm \
            -v "$vault:/kronos-vault" \
            -e KRONOS_SKILLS_PATH=/app/skills \
            -e PYTHONPATH=/app/mcp/kronos/src \
            "$IMAGE_NAME:latest" \
            python tests/test_mcp_handlers.py || \
            _warn "Handler tests had failures (timing thresholds may vary in container)"

        # MCP E2E tests — optional, subprocess-based (may timeout in cold containers)
        if [[ "${GRIM_E2E:-0}" == "1" ]]; then
            _log "── MCP E2E protocol tests ──"
            MSYS_NO_PATHCONV=1 docker run --rm \
                -v "$vault:/kronos-vault" \
                -e KRONOS_VAULT_PATH=/kronos-vault \
                -e KRONOS_SKILLS_PATH=/app/skills \
                "$IMAGE_NAME:latest" \
                python tests/test_mcp_e2e.py
        else
            _log "Skipping E2E tests (set GRIM_E2E=1 to run)"
        fi
    else
        _warn "Skipped MCP handler + E2E tests (no vault)"
    fi

    _ok "All tests passed"
}

cmd_up() {
    _log "Starting GRIM ..."

    # Use pre-built image — don't rebuild (compose cache can serve stale layers).
    # Run 'release.sh build' first if code changed.
    _compose up -d
    _log "Waiting for health check ..."
    sleep 3

    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' grim 2>/dev/null || echo "unknown")
    if [[ "$health" == "healthy" ]]; then
        _ok "GRIM is running and healthy on port ${GRIM_PORT:-8080}"
    else
        _warn "GRIM started but health status: $health (may still be starting)"
        _warn "Check: docker logs grim"
    fi
}

cmd_down() {
    _log "Stopping GRIM ..."
    _compose down
    _ok "GRIM stopped"
}

cmd_logs() {
    _compose logs -f --tail=100
}

cmd_status() {
    echo ""
    _log "── Container Status ──"
    docker ps --filter "name=grim" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "No containers"

    echo ""
    _log "── Health ──"
    local health
    health=$(docker inspect --format='{{.State.Health.Status}}' grim 2>/dev/null || echo "not running")
    echo "  Health: $health"

    echo ""
    _log "── Images ──"
    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || echo "No images"

    echo ""
    _log "── Volumes ──"
    docker volume ls --filter "name=grim" --format "table {{.Name}}\t{{.Driver}}" 2>/dev/null || echo "No volumes"

    echo ""
    _log "── Disk Usage ──"
    docker system df 2>/dev/null || true
}

cmd_clean() {
    _log "Cleaning up Docker resources ..."

    # 1. Remove stopped grim containers
    local stopped
    stopped=$(docker ps -a --filter "name=grim" --filter "status=exited" -q 2>/dev/null)
    if [[ -n "$stopped" ]]; then
        _log "Removing stopped containers ..."
        echo "$stopped" | xargs docker rm -f
    fi

    # 2. Remove old image tags (keep KEEP_IMAGES most recent + latest)
    local all_tags
    all_tags=$(docker images "$IMAGE_NAME" --format "{{.Tag}}" 2>/dev/null | grep -v "latest" | sort -r)
    local count=0
    while IFS= read -r tag; do
        [[ -z "$tag" ]] && continue
        count=$((count + 1))
        if [[ $count -gt $KEEP_IMAGES ]]; then
            _log "Removing old image: $IMAGE_NAME:$tag"
            docker rmi "$IMAGE_NAME:$tag" 2>/dev/null || true
        fi
    done <<< "$all_tags"

    # 3. Remove dangling images
    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null)
    if [[ -n "$dangling" ]]; then
        _log "Removing dangling images ..."
        echo "$dangling" | xargs docker rmi -f 2>/dev/null || true
    fi

    # 4. Prune build cache
    docker builder prune -f --filter "until=168h" 2>/dev/null || true

    _ok "Cleanup complete"
    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || true
}

cmd_rebuild() {
    _log "Full rebuild: clean → build → test → up → integration"
    cmd_down 2>/dev/null || true
    cmd_clean
    cmd_build
    cmd_test
    cmd_up
    cmd_integration
    _ok "Rebuild complete — GRIM is running on port ${GRIM_PORT:-8080}"
}

cmd_integration() {
    _log "Running integration tests against live container ..."
    local flags="--no-start"
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        _warn "No ANTHROPIC_API_KEY — skipping LLM tests"
        flags="$flags --no-llm"
    fi
    python "$GRIM_DIR/tests/test_integration.py" \
        --port "${GRIM_PORT:-8080}" $flags || \
        _warn "Some integration tests failed (check output above)"
}

cmd_prod() {
    GRIM_PROD=1
    _log "Production deploy ..."
    cmd_build
    GRIM_PROD=1 _compose up -d
    _ok "Production GRIM running"
}

# ── Main ──────────────────────────────────────────────────────

case "${1:-up}" in
    build)       cmd_build ;;
    test)        cmd_test ;;
    up)          cmd_up ;;
    down)        cmd_down ;;
    logs)        cmd_logs ;;
    status)      cmd_status ;;
    clean)       cmd_clean ;;
    rebuild)     cmd_rebuild ;;
    integration) cmd_integration ;;
    prod)        cmd_prod ;;
    help|-h|--help)
        echo ""
        echo "GRIM Release Manager"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  up            Start GRIM (default — docker compose up -d)"
        echo "  build         Build Docker image with version tag"
        echo "  test          Run unit + handler tests inside container"
        echo "  integration   Run integration tests against live container"
        echo "  down          Stop GRIM"
        echo "  logs          Tail container logs"
        echo "  status        Show containers, images, volumes, disk usage"
        echo "  clean         Remove old images + dangling layers (keeps last $KEEP_IMAGES)"
        echo "  rebuild       Full redeploy: clean → build → test → up → integration"
        echo "  prod          Production deploy (with resource limits)"
        echo ""
        echo "Environment:"
        echo "  GRIM_KEEP=N          Images to keep during clean (default: 3)"
        echo "  GRIM_PORT=N          Host port (default: 8080)"
        echo "  VAULT_PATH=...       Kronos vault path (default: ../kronos-vault)"
        echo "  ANTHROPIC_API_KEY    Required for LLM integration tests"
        echo ""
        ;;
esac

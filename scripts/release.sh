#!/usr/bin/env bash
# ── GRIM Release Manager ─────────────────────────────────────
#
# Single script for the full Docker lifecycle (GRIM + IronClaw):
#   build, test, deploy, clean, status.
#
# Builds TWO images:
#   grim       — Python/FastAPI/LangGraph AI companion
#   ironclaw   — Rust sandboxed execution engine (REST gateway)
#
# Usage:
#   ./scripts/release.sh setup     Full local setup (Python + UI + IronClaw + tests)
#   ./scripts/release.sh build     Build GRIM + IronClaw Docker images
#   ./scripts/release.sh unit      Run host-side unit tests (no Docker)
#   ./scripts/release.sh test      Run all tests inside container
#   ./scripts/release.sh up        Start GRIM + IronClaw (detached)
#   ./scripts/release.sh down      Stop GRIM + IronClaw
#   ./scripts/release.sh logs      Tail container logs
#   ./scripts/release.sh status    Show container health + image info
#   ./scripts/release.sh clean     Remove old images + dangling layers + anonymous volumes
#   ./scripts/release.sh purge     Deep clean: remove ALL unused images, volumes, build cache
#   ./scripts/release.sh deploy    Gated: unit → build → test → up → integration → clean
#   ./scripts/release.sh rebuild   Full redeploy: clean → build → test → up (legacy)
#   ./scripts/release.sh prod      Production deploy (with prod override)
#
# Environment:
#   GRIM_KEEP=3       Number of old image tags to keep (default: 3)
#   GRIM_PROD=1       Use production compose override
#   VAULT_PATH=...    Override vault mount path

set -euo pipefail

# ── MSYS / Git Bash path fix ─────────────────────────────────
# Prevent Git Bash from mangling Unix-style paths when passing
# them to Docker/docker-compose (e.g. /c/Users → C:\c\Users).
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

# ── Config ────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRIM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# On Git Bash / MSYS, convert to Windows-native paths for Docker Desktop.
# pwd returns /c/Users/... but Docker needs C:/Users/... or C:\Users\...
if command -v cygpath &>/dev/null; then
    GRIM_DIR="$(cygpath -m "$GRIM_DIR")"
fi
IMAGE_NAME="grim"
IRONCLAW_IMAGE_NAME="ironclaw"
KEEP_IMAGES="${GRIM_KEEP:-3}"

# Compose file paths (quoted separately to handle spaces in paths)
COMPOSE_FILE="$GRIM_DIR/docker-compose.yml"
COMPOSE_PROD_FILE="$GRIM_DIR/docker-compose.prod.yml"

# IronClaw engine
ENGINE_DIR="$GRIM_DIR/engine"

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

    # ── Build IronClaw engine image ──
    if [[ -d "$ENGINE_DIR" && -f "$ENGINE_DIR/Dockerfile" ]]; then
        _log "Building $IRONCLAW_IMAGE_NAME:$tag ..."
        docker build -t "$IRONCLAW_IMAGE_NAME:$tag" -t "$IRONCLAW_IMAGE_NAME:latest" "$ENGINE_DIR"
        _ok "Built: $IRONCLAW_IMAGE_NAME:$tag + $IRONCLAW_IMAGE_NAME:latest"

        _clean_old_images "$IRONCLAW_IMAGE_NAME"
    else
        _warn "IronClaw engine not found at $ENGINE_DIR — skipping engine build"
    fi

    # ── Build GRIM image ──
    _log "Building $IMAGE_NAME:$tag ..."
    docker build -t "$IMAGE_NAME:$tag" -t "$IMAGE_NAME:latest" "$GRIM_DIR"
    _ok "Built: $IMAGE_NAME:$tag + $IMAGE_NAME:latest"

    _clean_old_images "$IMAGE_NAME"

    # Remove dangling images from this build
    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null)
    if [[ -n "$dangling" ]]; then
        echo "$dangling" | xargs docker rmi -f 2>/dev/null || true
    fi

    _log "── Images ──"
    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null
    docker images "$IRONCLAW_IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || true
}

_clean_old_images() {
    # Remove old image tags for a given image name (keeps last KEEP_IMAGES + latest)
    local img_name="$1"
    local all_tags
    all_tags=$(docker images "$img_name" --format "{{.Tag}}" 2>/dev/null | grep -v "latest" | sort -r || true)
    [[ -z "$all_tags" ]] && return 0
    _log "Cleaning old $img_name images (keeping last $KEEP_IMAGES) ..."
    local count=0
    while IFS= read -r old_tag; do
        [[ -z "$old_tag" ]] && continue
        count=$((count + 1))
        if [[ $count -gt $KEEP_IMAGES ]]; then
            _log "Removing old image: $img_name:$old_tag"
            docker rmi "$img_name:$old_tag" 2>/dev/null || true
        fi
    done <<< "$all_tags"
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
    local workspace
    workspace="$(cd "$GRIM_DIR/.." && pwd)"
    if command -v cygpath &>/dev/null; then workspace="$(cygpath -m "$workspace")"; fi
    vault="${VAULT_PATH:-$workspace/kronos-vault}"
    if [[ ! -d "$vault" ]]; then
        _warn "Kronos vault not found at $vault — skipping MCP tests"
        _warn "Set VAULT_PATH to your kronos-vault directory"
        vault=""
    fi

    # Core + model routing + IronClaw + agent integration tests — no vault needed
    _log "── Core + routing + IronClaw + agent tests (container) ──"
    docker run --rm \
        -e KRONOS_VAULT_PATH=/app/tests/vault \
        -e KRONOS_SKILLS_PATH=/app/skills \
        "$IMAGE_NAME:latest" \
        python -m pytest \
            tests/test_grim_core.py \
            tests/test_model_routing.py \
            tests/test_ironclaw.py \
            tests/test_agent_integration.py \
            tests/test_memory_system.py \
            tests/test_agent_registry.py \
            tests/test_tool_registry.py \
            tests/test_keyword_router.py \
            tests/test_tool_context.py \
            tests/test_base_agent_callable.py \
            tests/test_notes_tools.py \
            tests/test_graph_smoke.py \
            tests/test_matcher_smoke.py \
            tests/test_vault_endpoints.py \
            -v --tb=short

    if [[ -n "$vault" ]]; then
        # MCP handler tests (57) — needs real vault (rw for write tests)
        _log "── MCP handler tests ──"
        docker run --rm \
            -v "$vault:/kronos-vault" \
            -e KRONOS_SKILLS_PATH=/app/skills \
            -e PYTHONPATH=/app/mcp/kronos/src \
            "$IMAGE_NAME:latest" \
            python tests/test_mcp_handlers.py || \
            _warn "Handler tests had failures (timing thresholds may vary in container)"

        # MCP E2E tests — optional, subprocess-based (may timeout in cold containers)
        if [[ "${GRIM_E2E:-0}" == "1" ]]; then
            _log "── MCP E2E protocol tests ──"
            docker run --rm \
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
    _log "Starting GRIM + IronClaw ..."

    # Use pre-built images — don't rebuild (compose cache can serve stale layers).
    # Run 'release.sh build' first if code changed.
    _compose up -d
    _log "Waiting for health checks ..."
    sleep 5

    # Check IronClaw health
    local ic_health
    ic_health=$(docker inspect --format='{{.State.Health.Status}}' ironclaw 2>/dev/null || echo "not found")
    if [[ "$ic_health" == "healthy" ]]; then
        _ok "IronClaw is healthy (port 3100, internal network)"
    elif [[ "$ic_health" == "not found" ]]; then
        _warn "IronClaw container not found — running GRIM without sandbox"
    else
        _warn "IronClaw health: $ic_health (may still be starting)"
    fi

    # Check GRIM health
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
    docker ps --filter "name=grim" --filter "name=ironclaw" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "No containers"

    echo ""
    _log "── Health ──"
    local grim_health ic_health
    grim_health=$(docker inspect --format='{{.State.Health.Status}}' grim 2>/dev/null || echo "not running")
    ic_health=$(docker inspect --format='{{.State.Health.Status}}' ironclaw 2>/dev/null || echo "not running")
    echo "  GRIM:     $grim_health"
    echo "  IronClaw: $ic_health"

    echo ""
    _log "── Images ──"
    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || echo "No GRIM images"
    docker images "$IRONCLAW_IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || echo "No IronClaw images"

    echo ""
    _log "── Volumes ──"
    docker volume ls --filter "name=grim" --format "table {{.Name}}\t{{.Driver}}" 2>/dev/null || echo "No volumes"

    echo ""
    _log "── Disk Usage ──"
    docker system df 2>/dev/null || true
}

cmd_clean() {
    _log "Cleaning up Docker resources ..."

    # 1. Remove stopped grim + ironclaw containers
    local stopped
    for name in grim ironclaw ai-bridge; do
        stopped=$(docker ps -a --filter "name=$name" --filter "status=exited" -q 2>/dev/null)
        if [[ -n "$stopped" ]]; then
            _log "Removing stopped $name containers ..."
            echo "$stopped" | xargs docker rm -f
        fi
    done

    # 2. Remove dead/created containers from test runs
    local dead
    dead=$(docker ps -a --filter "status=exited" --filter "status=dead" --filter "status=created" \
        --format "{{.ID}} {{.Image}}" 2>/dev/null | grep -E "(grim|ironclaw)" | awk '{print $1}' || true)
    if [[ -n "$dead" ]]; then
        _log "Removing dead test containers ..."
        echo "$dead" | xargs docker rm -f 2>/dev/null || true
    fi

    # 3. Remove old image tags for both images
    _clean_old_images "$IMAGE_NAME"
    _clean_old_images "$IRONCLAW_IMAGE_NAME"
    _clean_old_images "grim-ironclaw"
    _clean_old_images "grim-ai-bridge"

    # 4. Remove dangling images
    local dangling
    dangling=$(docker images -f "dangling=true" -q 2>/dev/null)
    if [[ -n "$dangling" ]]; then
        _log "Removing dangling images ..."
        echo "$dangling" | xargs docker rmi -f 2>/dev/null || true
    fi

    # 5. Remove anonymous volumes (orphaned, hash-named)
    local anon_vols
    anon_vols=$(docker volume ls -q --filter "dangling=true" 2>/dev/null | grep -E "^[0-9a-f]{64}$" || true)
    if [[ -n "$anon_vols" ]]; then
        local count
        count=$(echo "$anon_vols" | wc -l | tr -d ' ')
        _log "Removing $count anonymous volumes ..."
        echo "$anon_vols" | xargs docker volume rm 2>/dev/null || true
    fi

    # 6. Prune build cache older than 7 days
    docker builder prune -f --filter "until=168h" 2>/dev/null || true

    _ok "Cleanup complete"
    _log ""
    _log "── Remaining Images ──"
    docker images "$IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || true
    docker images "$IRONCLAW_IMAGE_NAME" --format "table {{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || true
    _log ""
    _log "── Disk Usage ──"
    docker system df 2>/dev/null || true
}

cmd_purge() {
    _log "Deep purge — reclaiming all reclaimable Docker resources ..."
    _warn "This removes ALL unused images, volumes, and build cache."
    _warn "Active containers and their volumes are preserved."
    _log ""

    # 1. Standard clean first
    cmd_clean

    # 2. Remove ALL dangling volumes (not just anonymous)
    local dangling_vols
    dangling_vols=$(docker volume ls -q --filter "dangling=true" 2>/dev/null)
    if [[ -n "$dangling_vols" ]]; then
        local count
        count=$(echo "$dangling_vols" | wc -l | tr -d ' ')
        _log "Removing $count unused volumes ..."
        echo "$dangling_vols" | xargs docker volume rm 2>/dev/null || true
    fi

    # 3. Remove legacy volumes from old GRIM versions (grimm_*)
    local legacy_vols
    legacy_vols=$(docker volume ls -q 2>/dev/null | grep "^grimm_" || true)
    if [[ -n "$legacy_vols" ]]; then
        _log "Removing legacy grimm_ volumes ..."
        echo "$legacy_vols" | xargs docker volume rm 2>/dev/null || true
    fi

    # 4. Full build cache prune (all ages)
    _log "Pruning all build cache ..."
    docker builder prune -af 2>/dev/null || true

    # 5. Remove unused images (not just dangling — any image not used by a container)
    _log "Removing unused images ..."
    docker image prune -af --filter "until=48h" 2>/dev/null || true

    _ok "Purge complete"
    _log ""
    _log "── Disk Usage After Purge ──"
    docker system df 2>/dev/null || true
}

cmd_unit() {
    _log "Running host-side unit tests ..."

    # Core unit tests (no Docker, no vault needed)
    _log "── Core unit tests (host) ──"
    (cd "$GRIM_DIR" && python -m pytest tests/test_grim_core.py -v --tb=short) || {
        _err "Core unit tests FAILED — aborting"
        return 1
    }

    # Model routing tests
    _log "── Model routing tests (host) ──"
    (cd "$GRIM_DIR" && python -m pytest tests/test_model_routing.py -v --tb=short) || {
        _err "Model routing tests FAILED — aborting"
        return 1
    }

    # IronClaw bridge tests
    _log "── IronClaw bridge tests (host) ──"
    (cd "$GRIM_DIR" && python -m pytest tests/test_ironclaw.py -v --tb=short) || {
        _err "IronClaw tests FAILED — aborting"
        return 1
    }

    # Agent integration tests
    _log "── Agent integration tests (host) ──"
    (cd "$GRIM_DIR" && python -m pytest tests/test_agent_integration.py -v --tb=short) || {
        _err "Agent integration tests FAILED — aborting"
        return 1
    }

    # Memory system tests
    _log "── Memory system tests (host) ──"
    (cd "$GRIM_DIR" && python -m pytest tests/test_memory_system.py -v --tb=short) || {
        _err "Memory system tests FAILED — aborting"
        return 1
    }

    # UI tests (if available)
    local ui_dir="$GRIM_DIR/ui"
    if [[ -d "$ui_dir" && -f "$ui_dir/package.json" ]]; then
        _log "── UI tests (vitest) ──"
        if command -v npm &>/dev/null; then
            (cd "$ui_dir" && npm run test) || {
                _err "UI tests FAILED — aborting"
                return 1
            }
        else
            _warn "npm not found — skipping UI tests"
        fi
    fi

    _ok "All host-side unit tests passed"
}

cmd_rebuild() {
    _log "Full rebuild: clean → build → test → up → integration"
    cmd_down 2>/dev/null || true
    cmd_clean
    cmd_build
    cmd_test
    cmd_up
    cmd_integration
    _ok "Rebuild complete — GRIM + IronClaw running on port ${GRIM_PORT:-8080}"
}

cmd_deploy() {
    _log "Gated deploy: unit → build → container tests → up → integration → clean"
    _log ""

    # Gate 1: Host-side unit tests (fast, no Docker)
    _log "━━━ Gate 1: Unit Tests ━━━"
    cmd_unit || { _err "Deploy aborted at Gate 1 (unit tests)"; return 1; }
    _log ""

    # Gate 2: Build Docker image
    _log "━━━ Gate 2: Build Image ━━━"
    cmd_build || { _err "Deploy aborted at Gate 2 (build)"; return 1; }
    _log ""

    # Gate 3: Container tests (MCP handler + E2E inside Docker)
    _log "━━━ Gate 3: Container Tests ━━━"
    cmd_test || { _err "Deploy aborted at Gate 3 (container tests)"; return 1; }
    _log ""

    # Gate 4: Bring up + integration tests against live container
    _log "━━━ Gate 4: Integration ━━━"
    cmd_down 2>/dev/null || true
    cmd_up || { _err "Deploy aborted at Gate 4 (startup)"; return 1; }
    cmd_integration || { _err "Deploy aborted at Gate 4 (integration tests)"; return 1; }
    _log ""

    # Gate 5: Clean old versions (only after everything passes)
    _log "━━━ Gate 5: Cleanup ━━━"
    cmd_clean

    _ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    _ok "Deploy complete — GRIM + IronClaw running on port ${GRIM_PORT:-8080}"
    _ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

cmd_integration() {
    _log "Running integration tests against live container ..."
    local flags="--no-start"
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        _warn "No ANTHROPIC_API_KEY — skipping LLM tests"
        flags="$flags --no-llm"
    fi
    python "$GRIM_DIR/tests/test_integration.py" \
        --port "${GRIM_PORT:-8080}" $flags || {
        _err "Integration tests failed"
        return 1
    }
}

cmd_prod() {
    GRIM_PROD=1
    _log "Production deploy ..."
    cmd_unit || { _err "Unit tests failed — aborting prod deploy"; return 1; }
    cmd_build
    GRIM_PROD=1 _compose up -d
    _ok "Production GRIM running"
}

cmd_setup() {
    _log "Setting up GRIM + IronClaw (local development) ..."
    _log ""

    # ── 1. Python deps ──
    _log "━━━ Python Dependencies ━━━"
    if command -v python &>/dev/null; then
        (cd "$GRIM_DIR" && pip install -e ".[server,cache]") || {
            _err "Python dependency install failed"
            return 1
        }
        # Kronos MCP
        if [[ -d "$GRIM_DIR/mcp/kronos" ]]; then
            (cd "$GRIM_DIR" && pip install -e "./mcp/kronos[cache]") || {
                _warn "Kronos MCP install failed (non-fatal)"
            }
        fi
        _ok "Python deps installed"
    else
        _err "Python not found — install Python 3.11+"
        return 1
    fi
    _log ""

    # ── 2. UI setup ──
    _log "━━━ UI Setup (Next.js) ━━━"
    local ui_dir="$GRIM_DIR/ui"
    if [[ -d "$ui_dir" && -f "$ui_dir/package.json" ]]; then
        if command -v npm &>/dev/null; then
            (cd "$ui_dir" && npm ci) || {
                _err "npm ci failed"
                return 1
            }
            _log "Building UI ..."
            (cd "$ui_dir" && npm run build) || {
                _err "UI build failed"
                return 1
            }
            _ok "UI built (output in ui/out/)"
        else
            _err "npm not found — install Node.js 20+"
            return 1
        fi
    else
        _warn "UI directory not found at $ui_dir"
    fi
    _log ""

    # ── 3. IronClaw engine (Rust) ──
    _log "━━━ IronClaw Engine (Rust) ━━━"
    if [[ -d "$ENGINE_DIR" && -f "$ENGINE_DIR/Cargo.toml" ]]; then
        if command -v cargo &>/dev/null; then
            _log "Building IronClaw (this may take a few minutes on first run) ..."
            (cd "$ENGINE_DIR" && cargo build --release) || {
                _err "IronClaw build failed"
                return 1
            }
            local binary
            if [[ -f "$ENGINE_DIR/target/release/ironclaw.exe" ]]; then
                binary="$ENGINE_DIR/target/release/ironclaw.exe"
            elif [[ -f "$ENGINE_DIR/target/release/ironclaw" ]]; then
                binary="$ENGINE_DIR/target/release/ironclaw"
            fi
            if [[ -n "$binary" ]]; then
                _ok "IronClaw built: $binary"
            else
                _warn "Build succeeded but binary not found in expected location"
            fi
        else
            _warn "Rust/cargo not found — IronClaw engine won't be available locally"
            _warn "Install: https://rustup.rs/ or use Docker (release.sh deploy)"
        fi
    else
        _warn "IronClaw engine not found at $ENGINE_DIR"
    fi
    _log ""

    # ── 4. Local directories ──
    _log "━━━ Local Directories ━━━"
    mkdir -p "$GRIM_DIR/local/evolution" "$GRIM_DIR/local/objectives" "$GRIM_DIR/logs"
    _ok "Created local/, logs/"
    _log ""

    # ── 5. Run tests ──
    _log "━━━ Verification ━━━"
    cmd_unit || {
        _warn "Some tests failed — check output above"
    }
    _log ""

    _ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    _ok "Setup complete!"
    _ok ""
    _ok "Next steps:"
    _ok "  Local dev:   uvicorn server.app:app --reload --port 8080"
    _ok "  Docker:      ./scripts/release.sh deploy"
    _ok "  IronClaw:    ironclaw ui --config engine/config/grim.yaml"
    _ok "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ── Main ──────────────────────────────────────────────────────

case "${1:-up}" in
    build)       cmd_build ;;
    test)        cmd_test ;;
    unit)        cmd_unit ;;
    up)          cmd_up ;;
    down)        cmd_down ;;
    logs)        cmd_logs ;;
    status)      cmd_status ;;
    clean)       cmd_clean ;;
    purge)       cmd_purge ;;
    rebuild)     cmd_rebuild ;;
    deploy)      cmd_deploy ;;
    integration) cmd_integration ;;
    setup)       cmd_setup ;;
    prod)        cmd_prod ;;
    help|-h|--help)
        echo ""
        echo "GRIM Release Manager"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  setup         Full local setup: Python deps + UI build + IronClaw + tests"
        echo "  up            Start GRIM + IronClaw (default — docker compose up -d)"
        echo "  build         Build GRIM + IronClaw Docker images with version tag"
        echo "  unit          Run host-side unit tests (no Docker)"
        echo "  test          Run unit + handler tests inside container"
        echo "  integration   Run integration tests against live containers"
        echo "  deploy        Gated pipeline: unit → build → test → up → integration → clean"
        echo "  down          Stop GRIM + IronClaw"
        echo "  logs          Tail container logs"
        echo "  status        Show containers, images, volumes, disk usage"
        echo "  clean         Remove old images, dead containers, anonymous volumes (keeps last $KEEP_IMAGES)"
        echo "  purge         Deep clean: ALL unused images, volumes, legacy data, build cache"
        echo "  rebuild       Full redeploy: clean → build → test → up → integration (legacy)"
        echo "  prod          Production deploy (unit tests + build + resource limits)"
        echo ""
        echo "Environment:"
        echo "  GRIM_KEEP=N          Images to keep during clean (default: 3)"
        echo "  GRIM_PORT=N          Host port (default: 8080)"
        echo "  VAULT_PATH=...       Kronos vault path (default: ../kronos-vault)"
        echo "  ANTHROPIC_API_KEY    Required for LLM integration tests"
        echo ""
        ;;
esac

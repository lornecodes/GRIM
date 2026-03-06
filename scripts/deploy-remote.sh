#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# GRIM Remote Deploy — deploy to production server
#
# Usage:
#   ./scripts/deploy-remote.sh              # Full deploy (sync + build + up)
#   ./scripts/deploy-remote.sh sync         # Git pull repos on server
#   ./scripts/deploy-remote.sh build        # Build Docker images on server
#   ./scripts/deploy-remote.sh up           # Start production stack
#   ./scripts/deploy-remote.sh down         # Stop production stack
#   ./scripts/deploy-remote.sh status       # Show container status + health
#   ./scripts/deploy-remote.sh logs [svc]   # Tail logs (optional: service name)
#   ./scripts/deploy-remote.sh env          # Transfer .env to server
#   ./scripts/deploy-remote.sh oauth        # CLIProxyAPI OAuth setup (interactive)
#   ./scripts/deploy-remote.sh ssh          # Open SSH shell to server
#   ./scripts/deploy-remote.sh health       # Quick health check
#
# Environment:
#   GRIM_HOST=grim-server    SSH host (default: grim-server from ~/.ssh/config)
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

HOST="${GRIM_HOST:-grim-server}"
REMOTE_WS="/home/peter/repos/core_workspace"
REMOTE_GRIM="$REMOTE_WS/GRIM"
LOCAL_GRIM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_log()  { echo -e "\033[1;34m[deploy]\033[0m $*"; }
_ok()   { echo -e "\033[1;32m[deploy]\033[0m $*"; }
_warn() { echo -e "\033[1;33m[deploy]\033[0m $*"; }
_err()  { echo -e "\033[1;31m[deploy]\033[0m $*" >&2; }

# ── Commands ──────────────────────────────────────────────────

cmd_sync() {
    _log "Syncing repos on $HOST..."
    ssh "$HOST" bash <<REMOTE
cd $REMOTE_WS
for repo in GRIM kronos-vault fracton; do
    if [ -d "\$repo/.git" ]; then
        printf "  %-20s" "\$repo"
        result=\$(cd "\$repo" && git pull --ff-only 2>&1 | tail -1)
        echo "\$result"
    fi
done
REMOTE
    _ok "Sync complete"
}

cmd_env() {
    _log "Transferring .env to server..."
    if [[ ! -f "$LOCAL_GRIM/.env" ]]; then
        _err "No .env found at $LOCAL_GRIM/.env"
        _err "Create one from .env.example first"
        return 1
    fi
    scp "$LOCAL_GRIM/.env" "$HOST:$REMOTE_GRIM/.env"
    _ok ".env transferred to $HOST:$REMOTE_GRIM/.env"
}

cmd_build() {
    _log "Building Docker images on $HOST..."
    ssh "$HOST" "cd $REMOTE_GRIM && ./scripts/release.sh build"
    _ok "Build complete"
}

cmd_up() {
    _log "Starting production stack on $HOST..."
    ssh "$HOST" "cd $REMOTE_GRIM && GRIM_PROD=1 ./scripts/release.sh up"
    _ok "Production stack started"
    echo ""
    cmd_health
}

cmd_down() {
    _log "Stopping stack on $HOST..."
    ssh "$HOST" "cd $REMOTE_GRIM && GRIM_PROD=1 ./scripts/release.sh down"
    _ok "Stack stopped"
}

cmd_status() {
    ssh "$HOST" "cd $REMOTE_GRIM && ./scripts/release.sh status"
}

cmd_logs() {
    local service="${1:-}"
    if [[ -n "$service" ]]; then
        ssh "$HOST" "cd $REMOTE_GRIM && GRIM_PROD=1 docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=100 $service"
    else
        ssh "$HOST" "cd $REMOTE_GRIM && GRIM_PROD=1 docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=100"
    fi
}

cmd_health() {
    _log "Health check..."
    local health
    health=$(ssh "$HOST" "curl -sf http://localhost:8080/health 2>/dev/null" || echo "UNREACHABLE")
    if [[ "$health" == "UNREACHABLE" ]]; then
        _err "GRIM not responding on $HOST:8080"
        return 1
    else
        _ok "GRIM healthy: $health"
    fi
}

cmd_oauth() {
    _log "CLIProxyAPI OAuth Setup"
    _log ""
    _log "This opens an SSH tunnel so the OAuth callback works."
    _log "When the URL appears, open it in your LOCAL browser."
    _log ""
    _warn "Press Ctrl+C when done."
    _log ""

    # Forward OAuth callback port through SSH tunnel
    ssh -t -L 54545:127.0.0.1:54545 "$HOST" \
        "docker exec -it cliproxyapi /CLIProxyAPI/CLIProxyAPI --claude-login"
}

cmd_ssh() {
    ssh "$HOST"
}

cmd_deploy() {
    _log "═══════════════════════════════════════════════════════"
    _log "  Production Deploy to $HOST"
    _log "═══════════════════════════════════════════════════════"
    _log ""

    # Pre-flight: check .env exists on server
    ssh "$HOST" "test -f $REMOTE_GRIM/.env" 2>/dev/null || {
        _err ".env missing on server!"
        _err "Run first: ./scripts/deploy-remote.sh env"
        return 1
    }

    # 1. Sync code
    _log "━━━ Step 1: Sync ━━━"
    cmd_sync
    _log ""

    # 2. Build images
    _log "━━━ Step 2: Build ━━━"
    cmd_build
    _log ""

    # 3. Restart stack
    _log "━━━ Step 3: Deploy ━━━"
    ssh "$HOST" "cd $REMOTE_GRIM && GRIM_PROD=1 ./scripts/release.sh down" 2>/dev/null || true
    cmd_up
    _log ""

    # 4. Clean old images
    _log "━━━ Step 4: Cleanup ━━━"
    ssh "$HOST" "cd $REMOTE_GRIM && ./scripts/release.sh clean"
    _log ""

    _ok "═══════════════════════════════════════════════════════"
    _ok "  Deploy complete!"
    _ok ""
    _ok "  GRIM UI:    http://10.0.0.62:8080"
    _ok "  Health:     http://10.0.0.62:8080/health"
    _ok "  Bridge:     http://10.0.0.62:8318 (via SSH tunnel)"
    _ok "═══════════════════════════════════════════════════════"
}

# ── Main ──────────────────────────────────────────────────────

case "${1:-deploy}" in
    sync)       cmd_sync ;;
    env)        cmd_env ;;
    build)      cmd_build ;;
    up)         cmd_up ;;
    down)       cmd_down ;;
    status)     cmd_status ;;
    logs)       shift; cmd_logs "$@" ;;
    health)     cmd_health ;;
    oauth)      cmd_oauth ;;
    ssh)        cmd_ssh ;;
    deploy)     cmd_deploy ;;
    help|-h|--help)
        echo ""
        echo "GRIM Remote Deploy"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  deploy      Full pipeline: sync → build → restart (default)"
        echo "  sync        Git pull repos on server"
        echo "  build       Build Docker images on server"
        echo "  up          Start production stack"
        echo "  down        Stop production stack"
        echo "  status      Container status + health"
        echo "  logs [svc]  Tail logs (optional service filter)"
        echo "  health      Quick health check"
        echo "  env         Transfer .env to server"
        echo "  oauth       CLIProxyAPI OAuth setup (interactive)"
        echo "  ssh         Open SSH shell"
        echo ""
        echo "Environment:"
        echo "  GRIM_HOST   SSH host (default: grim-server)"
        echo ""
        ;;
    *)
        _err "Unknown command: $1"
        echo "Run '$0 help' for usage"
        exit 1
        ;;
esac

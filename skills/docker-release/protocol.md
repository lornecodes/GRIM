# Docker Release Protocol

> Agent skill for GRIM Docker lifecycle management.
> All operations go through `scripts/release.sh` — the single entry point.

## When This Applies

Any time GRIM needs to be built, tested in container, deployed, or cleaned up.
This is the path from "code changed" to "new GRIM is running."

## Prerequisites

1. Docker Desktop is running
2. Working directory is `GRIM/`
3. `.env` file exists with `ANTHROPIC_API_KEY` (for runtime, not builds)
4. `kronos-vault` exists at `../kronos-vault` (for MCP tests)
5. CLIProxyAPI authenticated (see `cliproxyapi` skill for OAuth setup)
6. Node.js 20+ installed locally (for UI development and testing)

## Operations

### Build
```bash
./scripts/release.sh build
```
- Multi-stage Dockerfile: Node.js (UI static export) + Python (backend)
- Stage 1: `node:20-slim` — `npm ci` + `npm run build` → static files in `ui/out/`
- Stage 2: `python:3.11-slim` — backend + `COPY --from=ui-build /ui/out/ ui/out/`
- Tags image with git hash + date (e.g., `grim:a0fbb0e-20260226`)
- Also tags as `grim:latest`
- Shows image size after build

### UI Tests (run locally before Docker build)
```bash
cd ui && npm run test
```
- 30 tests via vitest + jsdom + @testing-library/react
- 15 persistence tests (save/load/delete roundtrips)
- 9 session hook tests (create, switch, delete)
- 6 component tests (render, session switching, message restoration)
- Config: `ui/vitest.config.ts` (React plugin, path aliases, jsdom)
- Run `npm run test:watch` for development

### Test (containerised)
```bash
./scripts/release.sh test
```
- Runs core unit tests (mocked, no vault needed)
- Runs 57 MCP handler tests (needs real vault mounted)
- E2E tests optional: `GRIM_E2E=1 ./scripts/release.sh test`
- Handler timing thresholds may vary in container (cold-start)

### Deploy (Development)
```bash
./scripts/release.sh up
```
- Starts both GRIM and CLIProxyAPI via docker compose (detached)
- Waits for health check
- Uses `docker-compose.yml` (debug mode)
- If proxy shows 0 clients, run OAuth login (see `cliproxyapi` skill)

### Deploy (Production)
```bash
./scripts/release.sh prod
```
- Builds fresh image
- Starts with production overrides (resource limits, always-restart)
- Uses `docker-compose.yml` + `docker-compose.prod.yml`

### Full Redeploy
```bash
./scripts/release.sh rebuild
```
The one-command path: `down` -> `clean` -> `build` -> `test` -> `up`

### Stop
```bash
./scripts/release.sh down
```

### Status
```bash
./scripts/release.sh status
```
Shows: container health, images, volumes, disk usage.

### Clean
```bash
./scripts/release.sh clean
```
- Removes stopped grim containers
- Keeps last N image tags (default 3, set `GRIM_KEEP=N`)
- Removes dangling images
- Prunes build cache older than 7 days

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRIM_KEEP` | 3 | Image tags to keep during clean |
| `GRIM_PORT` | 8080 | Host port for GRIM server |
| `VAULT_PATH` | `../kronos-vault` | Kronos vault path |
| `GRIM_PROD` | 0 | Use production compose override |
| `GRIM_E2E` | 0 | Run E2E tests (subprocess-based) |

### Integration Tests
```bash
./scripts/release.sh integration
```
- Runs against the live container (must be running)
- 35 tests across 5 tiers:
  1. Infrastructure (health, UI, docs, 404)
  2. MCP connectivity (direct Kronos calls)
  3. REST chat (POST /api/chat + session continuity)
  4. WebSocket chat (streaming traces + tokens)
  5. Error handling (empty, missing, invalid)
- Requires `ANTHROPIC_API_KEY` for LLM tests (tiers 3-4)
- Use `--no-llm` to skip LLM-dependent tests

## Standard Workflow

1. Make code changes (backend or UI)
2. If UI changed: `cd ui && npm run test` — verify UI tests locally
3. If UI changed: `cd ui && npm run build` — verify static export succeeds
4. `./scripts/release.sh build` — build Docker image (includes UI build stage)
5. `./scripts/release.sh test` — verify backend tests in container
6. `./scripts/release.sh up` — deploy
7. `./scripts/release.sh integration` — verify live endpoints
8. Periodically: `./scripts/release.sh clean` — reclaim disk space

Or one command: `./scripts/release.sh rebuild` (does steps 4-7)

### UI Development (dev server)
```bash
cd ui && npm run dev    # Next.js on :3000, proxies API to :8080
```
- Requires GRIM backend running (Docker or local uvicorn)
- `.env.local` sets `NEXT_PUBLIC_GRIM_API=http://localhost:8080`
- Hot reload for UI changes, no Docker rebuild needed

## Windows Notes

- `release.sh` sets `MSYS_NO_PATHCONV=1` to prevent Git Bash path mangling
- Volume mounts handle spaces in paths (e.g., "Dawn Field Institute")
- Docker Desktop must be running (not just installed)

## Safety Rules

1. **Always test after build** — never deploy untested images
2. **Keep old images** — `clean` preserves the last GRIM_KEEP tags for rollback
3. **No secrets in images** — API keys come from `.env` at runtime, not build time
4. **Health check** — verify `/health` endpoint responds after deploy
5. **Production limits** — prod override caps memory at 4GB to prevent runaway

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing

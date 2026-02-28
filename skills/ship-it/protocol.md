# Ship It — Production Deployment Protocol

> The single command to go from code changes to a verified production system.
> Runs all test tiers, builds images, deploys, and verifies end-to-end.

## When This Applies

Any time code has changed and needs to go to production. This is the
full gated pipeline — unit tests through post-deploy verification.

## Prerequisites

1. Docker Desktop is running
2. Working directory is `GRIM/`
3. `kronos-vault` exists at `../kronos-vault`
4. CLIProxyAPI authenticated (one-time OAuth setup)
5. Node.js 20+ installed (for UI tests)
6. Rust toolchain installed (for IronClaw builds)
7. Python venv active with GRIM dependencies

## Pipeline Overview

```
Gate 1: Unit Tests (host-side, fast)
  ├─ Python: core + model routing + ironclaw + agent integration
  └─ UI: vitest (components, persistence, sessions)
     │
Gate 2: Docker Build
  ├─ IronClaw image (Rust multi-stage)
  └─ GRIM image (Node.js UI + Python backend)
     │
Gate 3: Service Health
  ├─ docker compose up -d
  ├─ Wait for all 5 services healthy
  └─ Verify: cliproxyapi, ai-bridge, redis, ironclaw, grim
     │
Gate 4: Integration Tests (live endpoints)
  ├─ Tier 1: Infrastructure (health, UI, docs, 404)
  ├─ Tier 2: MCP connectivity (Kronos calls)
  ├─ Tier 3: REST chat (POST /api/chat + sessions)
  ├─ Tier 4: WebSocket chat (streaming traces)
  └─ Tier 5: Error handling (empty, missing, invalid)
     │
Gate 5: Post-Deploy Verification
  ├─ IronClaw gateway health: GET /v1/health
  ├─ GRIM API: GET /health, GET /api/ironclaw/status
  ├─ UI serving: GET / returns HTML
  └─ WebSocket: ws://localhost:8080/ws connects
     │
Gate 6: Cleanup
  └─ Remove old images (keep last 3 tags)
```

## Execution

### Gate 1: Unit Tests

Run all test suites on the host (no Docker needed):

```bash
cd GRIM

# Python tests (4 suites)
python -m pytest tests/test_grim_core.py -q
python -m pytest tests/test_model_routing.py -q
python -m pytest tests/test_ironclaw.py -q
python -m pytest tests/test_agent_integration.py -q

# UI tests
cd ui && npx vitest run && cd ..
```

**Pass criteria**: All tests pass (0 failures). Currently:
- `test_grim_core.py`: 119 tests
- `test_model_routing.py`: 54 tests
- `test_ironclaw.py`: 51 tests
- `test_agent_integration.py`: 59 tests
- UI: 29 tests

**Total: ~312 tests must pass.**

If any test fails: STOP. Fix the failure before proceeding.

### Gate 2: Docker Build

Build both images:

```bash
# IronClaw engine (Rust multi-stage, ~142MB)
docker compose build ironclaw

# GRIM server (Node.js + Python multi-stage, ~626MB)
docker compose build grim
```

**Pass criteria**: Both images build with exit code 0.

If build fails: STOP. Fix compilation/build errors.

### Gate 3: Service Health

Deploy all services and verify health:

```bash
docker compose up -d
```

Wait for all 5 services to report healthy:
1. **cliproxyapi** — `GET /v1/models` responds
2. **ai-bridge** — `GET /health` responds
3. **kronos-redis** — `redis-cli ping` → PONG
4. **ironclaw** — `GET /v1/health` → `{"status":"healthy"}`
5. **grim** — `GET /health` responds

```bash
# Check all services
docker ps --format "table {{.Names}}\t{{.Status}}"
```

**Pass criteria**: All 5 containers show `(healthy)`.

If any service unhealthy: check logs with `docker logs <name>`, fix, restart.

### Gate 4: Integration Tests

Run live endpoint tests against the running stack:

```bash
./scripts/release.sh integration
```

Or manually:
```bash
python -m pytest tests/test_integration.py -v
```

35 tests across 5 tiers:
- **Tier 1** (Infrastructure): health endpoint, UI serves, docs endpoint, 404 handling
- **Tier 2** (MCP): direct Kronos vault queries work
- **Tier 3** (REST chat): POST /api/chat returns response, sessions persist
- **Tier 4** (WebSocket): streaming connection, traces received
- **Tier 5** (Error handling): empty messages, missing fields, invalid sessions

Use `--no-llm` flag to skip LLM-dependent tests (tiers 3-4) if API key unavailable.

**Pass criteria**: All integration tests pass.

### Gate 5: Post-Deploy Verification

Final smoke test — verify all endpoints respond:

```bash
# IronClaw gateway
curl -s http://localhost:8080/api/ironclaw/status | python -m json.tool

# GRIM health
curl -s http://localhost:8080/health | python -m json.tool

# UI serves
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/

# IronClaw direct (from within Docker network)
docker exec grim python -c "
from core.bridge.ironclaw import IronClawBridge
import asyncio
async def check():
    b = IronClawBridge('http://ironclaw:3100', api_key='grim-internal-key')
    h = await b.health()
    print(f'IronClaw: healthy={h.healthy}, v{h.version}, uptime={h.uptime_secs}s')
    tools = await b.list_tools()
    print(f'Tools available: {len(tools)}')
    await b.close()
asyncio.run(check())
"
```

**Pass criteria**: All endpoints respond with 200. IronClaw reports healthy.

### Gate 6: Cleanup

Only after all gates pass:

```bash
./scripts/release.sh clean
```

Removes old images (keeps last 3 tags for rollback).

## Quick Deploy (scope=quick)

Skip container tests for faster iteration:

```
Gate 1 → Gate 2 → Gate 3 → Gate 5 → Gate 6
```

## Test Only (scope=test-only)

Run all tests without deploying:

```
Gate 1 only
```

## Using release.sh

The `release.sh` script wraps most of this:

```bash
# Full gated deploy (Gates 1-6)
./scripts/release.sh deploy

# Just unit tests (Gate 1)
./scripts/release.sh unit

# Just build (Gate 2)
./scripts/release.sh build

# Start services (Gate 3)
./scripts/release.sh up

# Integration tests (Gate 4)
./scripts/release.sh integration

# Cleanup (Gate 6)
./scripts/release.sh clean

# Full local setup (one-time)
./scripts/release.sh setup
```

## Failure Recovery

| Gate | Failure | Action |
|------|---------|--------|
| 1 | Test failure | Fix code, re-run tests |
| 2 | Build error | Fix Dockerfile or source, rebuild |
| 3 | Service unhealthy | Check `docker logs <name>`, fix config |
| 4 | Integration failure | May be transient — retry once, then investigate |
| 5 | Verification failure | Service may need more startup time — wait and retry |
| 6 | Cleanup failure | Non-critical — can be run manually later |

## Rollback

If post-deploy issues are discovered:

```bash
# Stop current deployment
docker compose down

# Redeploy previous image
docker tag grim:<previous-tag> grim:latest
docker compose up -d
```

## Vault Sync

After shipping, update affected vault FDOs:
- `proj-grim` — update test counts, phase status
- `grim-architecture` — if services or architecture changed
- `grim-server-ui` — if endpoints or UI changed
- `engine-ironclaw` — if IronClaw config or integration changed

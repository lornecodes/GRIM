# CLIProxyAPI — Proxy Lifecycle Management

> **Skill**: `cliproxyapi`
> **Version**: 1.0
> **Purpose**: Manage the CLIProxyAPI OAuth proxy for Claude Max subscription access.

---

## When This Applies

Activate this skill when:
- First-time setup of GRIM Docker stack (OAuth login needed)
- Proxy returns empty models list (`{"data":[],"object":"list"}`)
- GRIM gets 404 errors on LLM calls
- User asks about proxy status, OAuth, or Claude Max connectivity
- Container logs show `0 clients` or auth directory errors

## Architecture

```
Browser ──OAuth──► claude.ai
                      │
                      ▼ callback
Host:54545 ──────► cliproxyapi:54545  (one-time login only)

GRIM ──────────────► cliproxyapi:8317/v1/messages  (internal Docker network)
```

- **Port 8317**: API proxy (internal only, `expose` not `ports`)
- **Port 54545**: OAuth callback (localhost-only, one-time setup)
- **Network**: `grim-net` bridge — services reach each other by name
- **Auth volume**: `cliproxyapi-auths` → `/root/.cli-proxy-api/`
- **Config**: `config/cliproxyapi.yaml` → `/CLIProxyAPI/config.yaml`

## Prerequisites

1. Docker Desktop is running
2. `cliproxyapi` container exists (`docker compose up -d`)
3. Container is healthy (check: `docker ps --filter name=cliproxyapi`)

## Operations

### OAuth Login (First-Time or Re-Auth)

**PowerShell** (recommended on Windows):
```powershell
docker exec -it cliproxyapi /CLIProxyAPI/CLIProxyAPI --claude-login
```

**Git Bash** (requires MSYS path fix):
```bash
MSYS_NO_PATHCONV=1 docker exec -it cliproxyapi /CLIProxyAPI/CLIProxyAPI --claude-login
```

**Flow:**
1. CLI prints an authorization URL → open it in browser
2. Complete OAuth on claude.ai
3. Browser redirects to `http://localhost:54545/callback?code=...`
4. If auto-callback fails, CLI prompts "Paste the Claude callback URL"
5. Copy the **full URL from browser address bar** (the one with `?code=...`) and paste it
6. CLI confirms "Authentication successful"

**Critical**: Do NOT paste the original authorize URL back. Paste the **callback URL** (the one your browser landed on after OAuth).

**Verify tokens saved:**
```bash
# Check auth directory (should have a .json file)
docker exec cliproxyapi ls /root/.cli-proxy-api/
# Expected: claude-<email>.json
```

The proxy's file watcher auto-detects new auth files — no restart needed.

### Status Check

```bash
# Check if proxy has clients loaded
docker logs cliproxyapi --tail 5

# Look for: "N clients (N auth entries + ...)"
# If 0 clients: OAuth login needed

# Check available models
docker exec grim python -c "
import urllib.request
r = urllib.request.urlopen('http://cliproxyapi:8317/v1/models')
print(r.read().decode()[:300])
"

# Expected: {"data":[{"id":"claude-sonnet-4-6",...}, ...]}
# If {"data":[]}: auth tokens missing or expired
```

### Troubleshoot

**Problem: Empty models list (`{"data":[]}`)**
- Cause: No auth tokens. Run OAuth login (see above).
- Check: `docker exec cliproxyapi ls /root/.cli-proxy-api/` — should have `.json` files.

**Problem: `404 page not found` on LLM calls**
- Cause: Double `/v1` in URL path. Check proxy logs:
  ```bash
  docker logs cliproxyapi --tail 10
  # Look for: POST "/v1/v1/messages" — this is the bug
  ```
- Fix: `ANTHROPIC_BASE_URL` must be `http://cliproxyapi:8317` (NO `/v1` suffix).
  The Anthropic SDK adds `/v1/` automatically.
- After fixing in `docker-compose.yml`, recreate (not just restart):
  ```bash
  docker compose up -d grim
  ```
  Note: `docker compose restart` does NOT re-read compose file env vars.

**Problem: `failed to create auth directory : mkdir : no such file or directory`**
- Cause: `auth-dir` missing from `config/cliproxyapi.yaml`.
- Fix: Ensure config has `auth-dir: "/root/.cli-proxy-api"`.

**Problem: MSYS path mangling (Git Bash)**
- Symptom: `/CLIProxyAPI/CLIProxyAPI` becomes `C:/Program Files/Git/CLIProxyAPI/...`
- Fix: Prefix with `MSYS_NO_PATHCONV=1`. Not needed in PowerShell.

**Problem: OAuth callback not received**
- Check port mapping: `docker port cliproxyapi` — should show `54545/tcp -> 127.0.0.1:54545`
- Check nothing else is using port 54545
- Fallback: Copy callback URL from browser address bar and paste manually

## Key Configuration

**`docker-compose.yml` environment for GRIM:**
```yaml
- ANTHROPIC_BASE_URL=http://cliproxyapi:8317    # NO /v1 suffix!
- ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-sk-placeholder-for-proxy}
```

**`config/cliproxyapi.yaml`:**
```yaml
host: ""
port: 8317
auth-dir: "/root/.cli-proxy-api"
api-keys: []    # no client auth — internal network only
```

## Adding New Services (e.g., IronClaw)

Any new service on `grim-net` can share the proxy:
```yaml
new-service:
  environment:
    - ANTHROPIC_BASE_URL=http://cliproxyapi:8317
    - ANTHROPIC_API_KEY=sk-placeholder-for-proxy
  networks:
    - grim-net
```

## Safety Rules

1. **Never expose port 8317 to host** — internal Docker network only
2. **Port 54545 is localhost-only** — mapped as `127.0.0.1:54545:54545`
3. **Never log or display auth token contents** — only check file existence
4. **Recreate, don't restart** — env var changes need `docker compose up -d`, not `restart`

## Vault Sync

After infrastructure or proxy configuration changes, check if vault FDOs need updating:
1. Did the proxy setup, ports, or architecture change? Update [[proj-grim]] or deployment FDOs
2. If new services were connected, update the architecture in relevant FDOs
3. Update `updated:` dates on any modified FDOs

> Skipping this step is how FDOs drift from reality. If you changed something meaningful, sync it.

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Port numbers match `docker-compose.yml` and `config/cliproxyapi.yaml`
- [ ] If anything is stale, update this protocol before finishing

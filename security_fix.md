# Security + Lint Review (Prioritized Fix Plan)

Date: 2026-02-22  
Scope reviewed: `server/app/*.py`, `server/app/agent/*.py`, `client/src/**/*.ts(x)`, project dependency manifests.

## P0 (Critical) — fix immediately

### 1) WebSocket endpoints are unauthenticated and unscoped
- **Risk:** Anyone who can reach the service can open `/ws` and `/ws/chat`, send arbitrary events, and drive backend compute/transcription flows.
- **Evidence:** Endpoints accept connections with no auth/token/origin checks (`await websocket.accept()` immediately). See `server/app/main.py` and `server/app/chat_endpoint.py`.
- **Fix:**
  - Require a signed auth token (JWT or HMAC session token) in query params/headers before `accept()`.
  - Reject unauthorized origins and enforce allowed hostnames.
  - Add per-session ACL checks for privileged event types.

### 2) CORS is fully open while credentials are allowed
- **Risk:** Browser-based cross-origin abuse and unsafe credentialed cross-site requests.
- **Evidence:** `allow_origins=["*"]` with `allow_credentials=True`, plus all methods/headers allowed in `server/app/main.py`.
- **Fix:**
  - Replace wildcard origin with explicit allowlist from environment.
  - Disable `allow_credentials` unless strictly required.
  - Restrict methods/headers to minimum needed.

### 3) Sensitive conversation data is logged/written in plaintext
- **Risk:** PII leakage through logs and local files, especially mortgage details and transcript content.
- **Evidence:**
  - Raw frame snippets are logged on validation failure and payload data is logged for UI actions in `server/app/main.py`.
  - Raw Bedrock stream events are appended to `bedrock_stream.log` in `server/app/main.py`.
- **Fix:**
  - Remove raw payload logging or redact values (`text`, `image`, financial values).
  - Gate debug logging behind env flag (default off).
  - Route logs to structured logger with PII redaction policy and retention controls.

## P1 (High)

### 4) No explicit message size/rate limits for text/audio/image payloads
- **Risk:** Memory/CPU abuse and denial-of-service by oversized `client.text`, `image`, and repeated `client.audio.chunk` payloads.
- **Evidence:**
  - Input fields are accepted and appended directly to state without explicit size caps in both websocket handlers.
  - `client.audio.chunk` forwarding has no max payload length or per-interval throttle.
- **Fix:**
  - Enforce max frame sizes and payload length checks server-side.
  - Add per-connection rate limiting (token bucket/leaky bucket).
  - Drop/close abusive sessions and emit monitored security events.

### 5) Session identifiers are predictable/non-cryptographic
- **Risk:** Easier session enumeration/correlation in logs and telemetry.
- **Evidence:** `session_id = f"sess_{id(websocket)}"` and `sid = f"chat_{id(websocket)}"`.
- **Fix:**
  - Use `secrets.token_urlsafe(24)` or UUIDv4 for external session IDs.
  - Keep internal object IDs private and never emit them to clients.

### 6) Dependency hygiene is weak (unpinned Python deps)
- **Risk:** Uncontrolled upgrades and supply-chain instability.
- **Evidence:** `server/requirements.txt` uses unpinned package names.
- **Fix:**
  - Pin versions (or use constraints/lockfile tooling).
  - Run `pip-audit`/`safety` in CI on lockfile.
  - Add scheduled dependency update policy.

## P2 (Medium)

### 7) Client lint baseline is failing (23 issues, 16 errors)
- **Risk:** Reduced reliability/maintainability; some rule violations map to runtime risks.
- **Evidence:** `npm run lint` in `client` reports:
  - `react-hooks/immutability`: callback (`connect`) referenced before declaration.
  - `react-hooks/set-state-in-effect`: multiple synchronous `setState` in effects.
  - `@typescript-eslint/no-explicit-any`: multiple unsafe `any` usages in renderers/hook.
  - `react-hooks/exhaustive-deps`: missing dependencies.
- **Fix:**
  - Refactor hook initialization order and effect patterns.
  - Replace `any` with strict interfaces for A2UI payload/events.
  - Make `npm run lint` pass in CI before merge.

### 8) Hardcoded client session ID
- **Risk:** telemetry confusion and potential backend assumptions around identity.
- **Evidence:** Client sends `sessionId: 'init-123'` for multiple event types in `client/src/hooks/useMortgageSocket.ts`.
- **Fix:**
  - Remove client-provided session authority; backend should mint and own IDs.
  - If client correlation is needed, use random ephemeral client ID.

## Suggested execution order
1. **Auth + origin hardening** (P0 #1, #2).  
2. **PII logging controls** (P0 #3).  
3. **Input limits + rate limiting + secure IDs** (P1 #4, #5).  
4. **Dependency pinning + CI audit** (P1 #6).  
5. **Lint debt burn-down and strict typing** (P2 #7, #8).

## Checks run
- `cd client && npm run lint` → failed with 23 issues (used for lint findings).
- `cd client && npm audit --audit-level=moderate` → unable to complete due to 403 from npm advisory endpoint in this environment.
- `python -m pip install --quiet pip-audit && python -m pip_audit -r server/requirements.txt` → unable to install `pip-audit` due to proxy/index access limitations.

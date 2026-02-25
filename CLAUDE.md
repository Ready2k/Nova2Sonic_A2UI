# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A voice-first, A2UI-driven mortgage assistant demo. A Python FastAPI/LangGraph backend connects to a Next.js client over WebSockets. The agent collects mortgage details through conversation and generates a dynamic UI (A2UI) in real time.

## Commands

### Service Management (recommended)
```bash
./manage.sh start    # Start both server and client (logs → ./logs/)
./manage.sh stop     # Kill both
./manage.sh restart
./manage.sh status
./manage.sh errors   # Tail recent errors from logs
```

### Server (manual)
```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

### Client (manual)
```bash
cd client
npm install
npm run dev       # http://localhost:3000
npm run lint      # eslint
npm run build     # production build
```

### Tests
```bash
# Unit tests (mortgage math, tools) – run from server/
cd server
pytest tests/test_math.py -v

# Integration / goal-based WebSocket tests – requires server running on :8000
cd tests
python run_tests.py              # all scenarios
python run_tests.py GBT-FTB-01  # one scenario
python run_tests.py --list       # list available scenario IDs
```

## Architecture

### Request Flow
1. User speaks or types in the Next.js client
2. Client sends a WebSocket message (e.g. `client.audio.chunk`, `client.text`, `client.ui.action`)
3. Server (`server/app/main.py`) dispatches to STT (Nova Sonic), then invokes the LangGraph agent
4. Agent appends events to `state.outbox`; `process_outbox()` flushes them: non-voice events first, then a single merged TTS call
5. Client receives `server.a2ui.patch` (UI update), `server.voice.say` → audio chunks, and transcript messages

### WebSocket Protocol
All messages conform to `WebSocketMessage` (`server/app/models.py`): `{ type, sessionId, payload }`.

| Direction | Message type | Purpose |
|---|---|---|
| Client → Server | `client.audio.start/chunk/stop` | Stream PCM16 audio for STT |
| Client → Server | `client.audio.interrupt` | Cancel in-flight TTS |
| Client → Server | `client.text` | Text input (supports base64 image) |
| Client → Server | `client.ui.action` | Button/slider events from A2UI |
| Client → Server | `client.mode.update` | Switch between voice/text mode |
| Server → Client | `server.a2ui.patch` | Full A2UI component tree update |
| Server → Client | `server.voice.audio` | Base64 PCM16 audio chunk |
| Server → Client | `server.voice.start/stop` | TTS lifecycle signals |
| Server → Client | `server.transcript.partial/final` | Real-time transcription |
| Server → Client | `server.agent.thinking` | Spinner state |

### STT / TTS (Nova Sonic)
- **STT**: `server/nova_sonic_stt.mjs` — Node.js process spawned by `NovaSonicSession` (`server/app/nova_sonic.py`). PCM16 audio is piped in via stdin; `TRANSCRIPT:` and `TRANSCRIPT_PARTIAL:` lines come back on stdout.
- **TTS**: `server/nova_sonic_tts.mjs` — separate Node.js process spawned by `run_tts_inline()`. Outputs `AUDIO_CHUNK:<base64>` lines on stdout, which are forwarded as `server.voice.audio` WebSocket messages.
- Both Node scripts require AWS credentials (`AWS_ACCESS_KEY_ID` / `AWS_PROFILE`) and `AWS_REGION`.

### LangGraph Agent (`server/app/agent/graph.py`)
State machine compiled into `app_graph`. `AgentState` is the single source of truth.

**Key state fields:** `mode`, `transcript`, `messages`, `intent` (propertyValue, loanBalance, fixYears, termYears, existingCustomer, propertySeen, address, lat, lng, notes), `ltv`, `products`, `selection`, `ui`, `pendingAction`, `outbox`, `trouble_count`, `show_support`.

**Graph nodes:**
- `ingest_input` → pass-through, routes via `start_router`
- `interpret_intent` → calls Amazon Nova Lite (Bedrock) with structured output (`MortgageIntent`) to extract fields from the transcript; falls back to keyword matching without AWS
- `render_missing_inputs` → emits the initial category-selection grid or the detail-collection form (with map, property insights, green reward when address is known); calls Nova Lite to generate a contextual question
- `call_mortgage_tools` → computes LTV, fetches products (`server/app/agent/tools.py`)
- `render_products_a2ui` → emits COMPARISON UI with Gauge, ProductCards, DataCard; calls Nova Lite for a product intro
- `handle_ui_action` → dispatches `pendingAction` (update_term, select_product, confirm_application, reset_flow, select_category)
- `recalculate_and_patch` → re-runs amortisation and patches ProductCards only
- `render_summary_a2ui` → emits SUMMARY (AiP) screen
- `confirm_application` → emits CONFIRMED screen
- `clear_pending_action` → always the final node before END

**Routing:**
- On new transcript: `start_router` → `interpret_intent` → `intent_router` (missing fields → `render_missing_inputs`; else → `call_mortgage_tools`)
- On UI action: `start_router` → `handle_ui_action` → `ui_action_router`

**Trouble detection:** `interpret_intent` increments `trouble_count` when the user provides no useful info or uses struggle keywords. `show_support` becomes `true` at count ≥ 2, which makes the client show a "Speak to a Colleague" FAB.

**Refusal handling:** After each Nova Lite call, the agent checks the response and transcript for guardrail keywords and substitutes a safe fallback message.

### A2UI Renderer (`client/src/components/A2Renderer.tsx`)
The server emits a flat list of components with parent–child references. `A2Renderer` builds a `Map<id, component>` and recursively renders from the `"root"` node.

Supported component types: `Column`, `Row`, `Text` (variants: h1/h2/h3/body), `Gauge`, `ProductCard`, `Button`, `Map` (Leaflet iframe), `Timeline`, `DataCard`, `BenefitCard`, `ComparisonBadge`, `Image`.

Missing component types render as a visible red error box (not a crash).

`Button` components fire `onAction(id, data)` which sends `client.ui.action`. When `component.data.action` is set (e.g. category buttons), `handle_ui_action` uses that field as the action ID instead of the button's component ID.

### Client Hook (`client/src/hooks/useMortgageSocket.ts`)
`useMortgageSocket` manages the WebSocket, audio capture pipeline, and `AudioStreamer`.

- Recording uses `AudioWorkletNode` (`PCM16Processor`) at 16 kHz; chunks are base64-encoded and sent as `client.audio.chunk`.
- VAD: auto-stops after 1500 ms of silence below `0.015` RMS (only after speech has been detected).
- `AudioStreamer` queues base64 PCM16 chunks, schedules them on a 24 kHz `AudioContext`, and calls `finishPlayback()` when `server.voice.stop` arrives. After playback ends, mic auto-restarts in voice mode.
- The connection is not auto-established on mount; the user must click "Connect".

### Environment Variables
| Variable | Where used | Purpose |
|---|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | server | Bedrock + Nova Sonic |
| `AWS_REGION` | server | Defaults to `us-east-1` |
| `AWS_PROFILE` | server | Alternative to key/secret |
| `AGENT_MODEL_ID` | server | Defaults to `amazon.nova-lite-v1:0` |
| `DESIGNER_SONNET_MODEL_ID` | server | A2UI design model. Defaults to `us.anthropic.claude-sonnet-4-6-20251101-v1:0` |
| `DESIGNER_HAIKU_MODEL_ID` | server | A2UI design fallback model. Defaults to `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `NEXT_PUBLIC_WS_URL` | client | Defaults to `ws://localhost:8000/ws` |

### Key Files
```
server/app/main.py              # WebSocket endpoint, session management, outbox processor
server/app/agent/graph.py       # LangGraph nodes, routers, compiled app_graph
server/app/agent/tools.py       # LTV calc, amortisation, product DB
server/app/nova_sonic.py        # NovaSonicSession (STT subprocess wrapper)
server/nova_sonic_stt.mjs       # Node.js STT script (AWS Nova Sonic)
server/nova_sonic_tts.mjs       # Node.js TTS script (AWS Nova Sonic)
client/src/hooks/useMortgageSocket.ts  # WS + audio pipeline hook
client/src/components/A2Renderer.tsx   # A2UI component renderer
client/src/app/page.tsx                # Main page layout
tests/scenarios.py              # Integration test scenarios (WS-based)
tests/harness.py                # Test harness utilities
graph_blueprint.md              # Original LangGraph design spec
```

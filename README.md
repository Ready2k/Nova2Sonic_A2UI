# Barclays Mortgage Assistant Demo

A voice-first, A2UI-driven mortgage assistant demonstration. The agent conducts a natural conversation (voice or text) to collect mortgage details, then generates a fully dynamic UI in real time — including property maps, LTV gauges, mortgage product comparison cards, and a summary screen — all rendered from a JSON component tree emitted by the backend.

## Features

- **Dual-mode interaction** — switch between voice and text at any time during a session
- **Real-time speech-to-text** — audio streamed from the browser is transcribed via Amazon Nova 2 Sonic
- **AI intent extraction** — Amazon Nova Lite (Bedrock) extracts structured mortgage intent from conversational input, handling corrections, short answers, and natural phrasing
- **Dynamic UI generation (A2UI)** — the LangGraph agent emits a flat JSON component tree on every turn; the Next.js client renders it without any client-side routing or state management
- **Voice synthesis** — agent responses are spoken back using Amazon Nova 2 Sonic TTS, with barge-in support (speaking starts a new mic session immediately)
- **Mortgage category flows** — First-time buyer, Remortgage, Buy-to-let, and Moving Home, each with tailored question sequences
- **Property enrichment** — once an address is provided, the UI shows an embedded Leaflet map, EPC/Council Tax data cards, and a green mortgage cashback reward badge
- **Mortgage comparison** — LTV gauge, up to two matched Barclays products (Feb 2026 rates), amortisation-calculated monthly payments, and a term slider (5–40 years) with live recalculation
- **Agreement in Principle summary** — selected product summary with regulatory disclaimer
- **Struggle detection** — after repeated non-productive turns, a "Speak to a Colleague" call-to-action appears
- **Latency HUD** — TTFB, UI patch latency, and voice latency displayed in the client for demo purposes

## Architecture

```
Browser (Next.js)  ──WebSocket──  FastAPI (Python)
      │                               │
  A2Renderer                    LangGraph agent
  AudioStreamer                  Nova Sonic STT (Node.js)
  useMortgageSocket              Nova Sonic TTS (Node.js)
                                 Bedrock Nova Lite (NLU)
```

All client ↔ server communication uses a single persistent WebSocket at `ws://localhost:8000/ws`. The server maintains per-session state; the client is stateless and renders whatever the server sends.

## Prerequisites

- **Node.js 18+** (tested on v24)
- **Python 3.10+** (tested on 3.14)
- **AWS account** with access to:
  - `amazon.nova-2-sonic-v1:0` (us-east-1) — STT and TTS
  - `amazon.nova-lite-v1:0` (us-east-1) — NLU / intent extraction

## AWS Setup

The server reads credentials from standard AWS environment variables or `~/.aws/credentials`. Create a `.env` file in the **project root** (next to `manage.sh`):

```bash
# .env  — loaded by both Python (python-dotenv) and Node (dotenv)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1

# Optional overrides
AGENT_MODEL_ID=amazon.nova-lite-v1:0   # NLU model
```

Alternatively, configure a named profile (`AWS_PROFILE=my-profile`) or use an IAM role if running on EC2/ECS.

The IAM policy needs:
```json
{
  "Effect": "Allow",
  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
  "Resource": [
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0",
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"
  ]
}
```

## Installation

### Server

```bash
cd server

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt

# Install Node dependencies (for STT/TTS scripts)
npm install
```

### Client

```bash
cd client
npm install
```

## Running

### Option A — Management script (recommended)

From the project root:

```bash
./manage.sh start     # starts both server (:8000) and client (:3000) in background
./manage.sh status    # check if both services are running
./manage.sh errors    # tail recent errors from logs/
./manage.sh stop      # kill both services
./manage.sh restart   # stop then start
```

Logs are written to `logs/server.log` and `logs/client.log`.

### Option B — Manual

In one terminal:
```bash
cd server
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

In a second terminal:
```bash
cd client
npm run dev
```

Open **http://localhost:3000** in a browser.

## Demo Walkthrough

1. Open `http://localhost:3000` and click **Connect**
2. Select a mortgage category (e.g. **First-time buyer**)
3. Answer the assistant's questions — either by typing or switching to **Voice Mode** and using the microphone button
4. Once all details are collected, the comparison screen appears with an LTV gauge and two product cards
5. Drag the **term slider** to see monthly payments recalculate in real time
6. Click a product card to proceed to the **Agreement in Principle** summary
7. Click **Confirm Application** to complete the demo flow

Example voice input for a fast happy path:
> *"I bank with Barclays. I've found a property at 42 Acacia Avenue, London. It's worth four hundred thousand, I need three hundred and fifty thousand, and I want a five year fix."*

## Tests

### Unit tests (mortgage maths)

```bash
cd server
source .venv/bin/activate
pytest tests/test_math.py -v
```

### Integration tests (WebSocket, goal-based)

The server must be running on `:8000`. These tests drive the full agent via WebSocket.

```bash
# From the project root
cd tests

python run_tests.py                        # run all scenarios
python run_tests.py GBT-FTB-01            # run one scenario by ID
python run_tests.py GBT-FTB-01 GBT-FTB-04 # run a subset
python run_tests.py --list                 # list all available scenario IDs
```

Available scenario IDs:

| ID | What it tests |
|---|---|
| GBT-FTB-01 | FTB basic quote: full happy path → LTV + 2 products |
| GBT-FTB-02 | Self-correction in transcript ("four twenty… sorry, 420k") |
| GBT-FTB-03 | Missing fixYears → agent asks exactly one question |
| GBT-FTB-04 | Term slider recalculation speed and patch content |
| GBT-FTB-05 | Product selection → summary state with disclaimer |
| GBT-FTB-06 | Confirm application → confirmed state + reset button |
| GBT-FTB-09 | Reset clears all state, category grid returns |
| GBT-FTB-10 | Invalid input (PV=0) — server does not crash |
| GBT-FTB-11 | Vague user — one question at a time, no overload |
| GBT-FTB-12 | TTFB performance — full response within 15 s |

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | — | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | — | AWS credentials |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock |
| `AWS_PROFILE` | — | Named AWS profile (alternative to key/secret) |
| `AGENT_MODEL_ID` | `amazon.nova-lite-v1:0` | Bedrock model used for NLU |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000/ws` | WebSocket URL for the client |

## Fallback Behaviour (No AWS)

If no AWS credentials are found, the server falls back to keyword-based intent parsing and skips TTS/STT. The text mode UI still functions end-to-end, but responses will use static template strings rather than generated language, and voice features will be unavailable.

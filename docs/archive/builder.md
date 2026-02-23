# BUILDER PROMPT — Build the Voice + A2UI Mortgage Demo (Barclays Mortgage Assistant)

You are an autonomous coding agent (Claude Code / Google Antigravity). Implement the **Barclays Mortgage Assistant** exactly per `SPEC.md` (the Mortgage Assistant spec). Deliver a working repo that runs locally and demonstrates: **voice to initiate, UI to manage mortgage options**.

---

## 0) Non-Negotiables
- **This is a Mortgage Assistant**. Do not implement restaurant search or any other domain.
- **Spec is law.** Use the exact event names, payload shapes, UI states, node names, and tool signatures from `SPEC.md`.
- Do not add features beyond the spec. No extra endpoints, no extra flows, no extra “helpful” UX.
- If something is ambiguous, choose the simplest option that still meets acceptance criteria and document it in `DECISIONS.md`.
- No TODOs for core flow. If Nova 2 Sonic isn’t available, implement deterministic mocks and note it in `KNOWN_LIMITATIONS.md`.

---

## 1) Deliverables
Create a repo with:
- `/client` — Next.js web app
- `/server` — FastAPI (Python) WebSocket server + LangGraph agent
- Root `README.md` — exact run steps + demo script
- `DECISIONS.md`
- `KNOWN_LIMITATIONS.md`
- `.env.example` (include placeholders even if mocks used)

Must run locally via:
- Client: `npm install && npm run dev`
- Server: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python -m app.main`
(or equivalent, but document precisely)

---

## 2) Canonical Naming (Hard Rule)
Use these exact field names everywhere:
- `propertyValue`
- `loanBalance`
- `fixYears`
- `termYears`

Do not introduce synonyms like `debt`, `currentDebt`, `principal` in the public event payloads or UI actions. Internally you may alias, but external contract stays canonical.

---

## 3) Event Contract (WebSocket) — Exact Implementation
All messages must include:
- `type` (string), `ts` (ISO8601), `sessionId` (string), `payload` (object)

Reject unknown `type` with `server.error`.

### Client → Server (must implement)
- `client.hello`
- `client.text`
- `client.audio.start`
- `client.audio.chunk` (base64 string in JSON)
- `client.audio.stop`
- `client.audio.interrupt`
- `client.ui.action`

### Server → Client (must implement)
- `server.ready`
- `server.transcript.final`
- `server.agent.thinking`
- `server.voice.say`
- `server.a2ui.patch`
- `server.error`

Optional but supported:
- `server.transcript.partial`
- `server.voice.stop`

### Allowed `client.ui.action` IDs (only these)
- `update_term` with `data: { "termYears": number }`
- `select_product` with `data: { "productId": string }`
- `confirm_application` (data optional)
- `reset_flow` (data optional)

---

## 4) Voice Requirements
- Default: **True S2S streaming** natively using Amazon Bedrock Nova 2 Sonic.
- Server emits `server.voice.audio` containing base64 PCM16 audio chunks, played via `AudioContext` on the frontend.
- Spoken responses must be **max 2 sentences**.
- Never read more than 3 product details aloud.
- When lists are bigger than 2 products (should not happen), say: “I’ve put the full comparison on screen.”
- Support **inline Blobs for AudioWorkletNode** to bypass Next.js script-src restrictions while processing PCM16 encoded capture.

### STT Requirements
- If Nova 2 Sonic credentials are not provided, implement **mock STT**:
  - Accept audio events
  - On `client.audio.stop`, emit `server.transcript.final` deterministically (fixed phrase or simple regex heuristic)
- Do not block the rest of the flow on real STT.

### Barge-in (must implement)
If Audio is playing and user clicks the microphone toggle to speak:
1) Client stops the `AudioContext` buffer queue.
2) Client sends `client.audio.interrupt` **before** `client.audio.start`
3) Server cancels any in-flight streaming for that turn and suppresses further `server.voice.audio`

---

## 5) A2UI Requirements
- UI must render a streamed surface with `surfaceId: "main"`.
- Server must drive UI exclusively via `server.a2ui.patch`. Client must NOT infer UI by parsing assistant text.
- Implement a **minimal A2UI renderer** sufficient for this demo:
  - **LTV Gauge/Progress** (percent + band label)
  - **Term Slider** (5–40 years, default 25)
  - **Comparison Cards** (rate, fee, monthly, total interest)
  - **Buttons** for apply/confirm/reset
- If you use an official A2UI renderer, keep integration minimal; otherwise render a strict subset and document it.

### UI States (must implement)
- **State 0: Loading**
  - Message “Calculating your options…”
  - Optional skeleton cards
- **State A: Product Comparison**
  - Header with `propertyValue`, `loanBalance`, `fixYears`, computed `ltv`
  - LTV gauge with band label
  - Term slider emitting `update_term`
  - Two product cards emitting `select_product`
- **State B: Application Summary**
  - Selected product summary + disclaimers
  - Buttons: `confirm_application`, `reset_flow`

---

## 6) LangGraph Agent Requirements
Implement nodes (minimum; names must match):
1) `ingest_input`
2) `interpret_intent`
3) `call_mortgage_tools`
4) `render_products_a2ui`
5) `handle_ui_action`
6) `render_summary_a2ui`
7) `voice_confirm`

### Agent State (must include)
- `mode` (voice|text)
- `transcript`
- `intent`: `{ propertyValue, loanBalance, fixYears, termYears }`
- `ltv`
- `products`
- `selection`: `{ productId, termYears }`
- `errors`

### Tool Signatures (mocked)
- `calculate_ltv(propertyValue, loanBalance) -> ltvPercentage`
- `fetch_mortgage_products(ltv, fixYears) -> products[]`
- `recalculate_monthly_payment(principal, annualRate, termYears, fee) -> { monthlyPayment, totalInterest, totalPaid }`

---

## 7) Deterministic Mortgage Math (Must Implement + Unit Test)
Compute:
- `ltv = (loanBalance / propertyValue) * 100` rounded to 1 decimal

Monthly payment (capital & interest):
- `r = annualRate / 12`
- `n = termYears * 12`
- `Monthly = [P * r * (1+r)^n] / [(1+r)^n - 1]`

Also compute:
- `totalPaid = (monthlyPayment * n) + fee`
- `totalInterest = totalPaid - P - fee`

Add unit tests for:
- LTV rounding correctness
- amortisation formula correctness (known expected values)

---

## 8) Deterministic Mock Products (Hard Requirement)
Return exactly 2 products:
- **Product A**: `"Barclays Standard Fix"`, `4.2%`, `£999` fee
- **Product B**: `"Barclays Premier Fix"`, `4.0%`, `£1499` fee

Use deterministic IDs:
- `prod_standard_fix`
- `prod_premier_fix`

---

## 9) Latency Requirements (Must Implement)
### Immediate loading patch
On `client.audio.stop` or `client.text`, server must immediately emit:
1) `server.agent.thinking` (`state: "rendering_ui"`)
2) `server.a2ui.patch` (State 0: Loading)
Then perform calculations and emit results.

### Slider responsiveness
In mocked mode, after receiving `update_term`, the server must emit updated `server.a2ui.patch` within **<150ms**.

### Latency HUD (client)
Client must display:
- TTFB ms (end of speech → first server event)
- UI first patch ms
- Voice start ms (when Web Speech API begins speaking)

---

## 10) Observability + Safety (Minimum)
Server logs:
- inbound/outbound event type + sessionId
- tool call durations
- per-turn latency
- error codes
Do not log raw audio bytes.

Client:
- WS connect/disconnect
- debug console showing last 20 events

Enforce:
- max WS payload size
- basic rate limiting for audio chunk flood

---

## 11) Definition of Done Gate (Run This Before Finishing)
1) Toggle **Voice Mode** on.
2) Speak: “My house is worth four hundred thousand and I owe two hundred and fifty thousand. I want a five year fix.”
3) Verify UI shows **62.5% LTV**.
4) Drag **Term Slider** to 30 years; verify monthly payments update via `server.a2ui.patch`.
5) Select **Barclays Standard Fix** and confirm application.
6) Test **Barge-in**: interrupt voice playback by clicking the microphone toggle; audio must stop immediately.

If any step fails, fix it before concluding.

---

## 12) Output Format When You Respond
Return:
1) Checklist of what was built
2) Exact run commands
3) Notes written into `DECISIONS.md`
4) Notes written into `KNOWN_LIMITATIONS.md`

Do not propose alternative architectures. Do not add extra features. Build what’s specified.

---
### 13) references for Nova2Sonic
# Nova2Sonic Integrations
https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-integrations.html

# Nova2Sonic Code Examples
https://github.com/aws-samples/amazon-nova-samples/blob/main/speech-to-speech/amazon-nova-2-sonic/sample-codes/console-python/nova_sonic_simple.py

# Nova2Sonic Code Examples
https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-code-examples.html

# Example of a voicebot using Nova2Sonic
https://github.com/aws-samples/sample-voicebot-nova-sonic

# Google A2UI
https://developers.googleblog.com/introducing-a2ui-an-open-project-for-agent-driven-interfaces/

# a2a
https://a2a-protocol.org/latest/specification/

# Gen UI overview
https://docs.flutter.dev/ai/genui

# Gen UI example
https://github.com/flutter/genui/tree/main/examples/verdure


# barclays websites
https://www.barclays.co.uk/mortgages/agreement-in-principle

# mortgage calculator
https://www.barclays.co.uk/mortgages/mortgage-calculator

# first time buyer rates
https://www.barclays.co.uk/mortgages/first-time-buyers/rates

# agreement in principle
https://www.barclays.co.uk/mortgages/agreement-in-principle

# fixed rate mortgage
https://www.barclays.co.uk/mortgages/fixed-rate-mortgage/

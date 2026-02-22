# SPEC — Voice + A2UI Demo (LangGraph + Barclays Mortgage Assistant)

## 0. Purpose
Build a working demo that combines:
- **Voice-first interaction** (Nova 2 Sonic STT/TTS or mock)
- **UI-rich streaming responses** (A2UI surfaces)
- **Agent orchestration** (LangGraph)
- **Real-time transport** (WebSocket, single session)

The demo proves that complex financial products (Mortgages) can be initiated via voice but managed through interactive UI, with measurable latency targets and barge-in support.

---

## 1. Goals and Non-Goals

### 1.1 Goals
The system must provide:
- **Web App**:
    - Push-to-talk voice input
    - Live transcript display
    - Short spoken guidance (max 2 sentences)
    - Streaming A2UI panel
    - Latency HUD (TTFB / UI / Voice)
    - Barge-in interrupt (stop TTS when user speaks)
- **Agent Capabilities**:
    - Calculate mortgage options (mocked) based on: Loan-to-Value (LTV), Fix period, and Mortgage term.
    - Support interactive: Term slider (5–40 years, default 25), Product comparison cards, and Apply / confirm actions.
- **Mode Support**:
    - Text Mode — typed input + UI
    - Voice Mode — speech + UI
    - The same agent graph must support both modes.

### 1.2 Non-Goals
- No real underwriting or affordability logic
- No regulated financial advice
- No production auth/KYC
- No persistence or multi-session memory
- No Amazon Connect integration
- No real product rates (mocked only)

---

## 2. Canonical Data Model (Mandatory Consistency)
To prevent naming drift, the following field names are mandatory everywhere:
- `propertyValue`
- `loanBalance`
- `fixYears`
- `termYears`

These names must be used consistently in: Agent state, Tool inputs, UI action payloads, A2UI patches, and Calculations.

---

## 3. User Experience Requirements

### 3.1 Primary Flow (Voice → UI Management)
**User says**: “I want to remortgage. My house is worth £400,000 and I owe £250,000. What are my options for a 5-year fix?”

**System Behaviour**:
1.  **Live Transcript**: Displays recognised values in real time.
2.  **Agent Voice (max 2 sentences)**: “Based on a 62.5% LTV, I’ve found two 5-year fixed options. Adjust the term on screen to see how payments change.”
3.  **UI — State A: Product Comparison**:
    - **Header**: Property value, Loan balance, Fix years, Computed LTV.
    - **LTV Gauge**: Percentage (rounded to 1 decimal) and Band label (e.g., 60–75%).
    - **Term Slider**: Range (5–40 years), Default (25 years).
    - **Two Product Cards (mocked)**: Rate, Fee, Monthly payment, Total interest.
4.  **User Interaction**: Move slider, Tap a card, or say “I’ll go with the first one.”
5.  **UI Update**: Slider movement must trigger `client.ui.action`, cause recalculation, and return new `server.a2ui.patch` within <150ms.
6.  **State B: Application Summary**: Selected product, Term, Monthly payment, LTV band, Disclaimer panel, and buttons (`confirm_application`, `reset_flow`). Voice confirmation must be ≤2 sentences.

---

## 4. Mortgage Computation Rules

### 4.1 LTV
`ltv = (loanBalance / propertyValue) * 100` (Round to 1 decimal for display).

### 4.2 Product Eligibility (Mocked)
- Return exactly 2 products based on LTV band + fixYears.
- Deterministic output only.

### 4.3 Monthly Payment (Capital & Interest)
Use standard amortisation formula:
`M = [P * r * (1 + r)^n] / [(1 + r)^n - 1]`

Where:
- `P` = principal (loanBalance)
- `r` = annualRate / 12
- `n` = termYears * 12

**Also compute**:
- `totalPaid = (monthlyPayment * n) + fee`
- `totalInterest = totalPaid - principal - fee`

---

## 5. Event Contract (WebSocket)
All messages must contain `type` (string), `ts` (ISO8601), `sessionId` (string), and `payload` (object). Unknown types must return `server.error`.

### 5.1 Client → Server
- `client.hello`: Initial handshake.
- `client.text`: Typed message.
- `client.audio.start`: Begin voice capture.
- `client.audio.chunk`: Base64 audio chunk.
- `client.audio.stop`: End capture.
- `client.audio.interrupt`: Sent before new speech if TTS is playing.
- `client.ui.action`:
```json
{
  "type": "client.ui.action",
  "payload": {
    "actionId": "update_term",
    "data": { "termYears": 30 }
  }
}
```

**Allowed actionIds**: `update_term`, `select_product`, `confirm_application`, `reset_flow`.

### 5.2 Server → Client
- `server.ready`
- `server.transcript.partial` (optional)
- `server.transcript.final`
- `server.agent.thinking`
- `server.voice.say`
- `server.voice.stop`
- `server.a2ui.patch`
- `server.error`

---

## 6. A2UI Surface Specification
- **Surface ID**: `main`

### 6.1 State 0: Loading
- Header (if values known)
- Message: “Calculating your options…”
- Optional skeleton cards
*This must be emitted immediately after `client.audio.stop`.*

### 6.2 State A: Product Comparison
- Header (`propertyValue`, `loanBalance`, `fixYears`, `LTV`)
- LTV Gauge
- Term Slider (emits `actionId: "update_term"`)
- Two comparison cards (emit `actionId: "select_product"`)

### 6.3 State B: Application Summary
- Selected product, Term, Monthly, LTV band, Disclaimer
- Buttons: `confirm_application`, `reset_flow`

---

## 7. Agent Design (LangGraph)
### 7.1 Required Nodes
1.  `ingest_input`
2.  `interpret_intent`
3.  `call_mortgage_tools`
4.  `render_products_a2ui`
5.  `handle_ui_action`
6.  `render_summary_a2ui`
7.  `voice_confirm`

### 7.2 State Model
- `mode`: voice | text
- `transcript`: string
- `intent`: `{ propertyValue, loanBalance, fixYears, termYears }`
- `ltv`: number
- `products`: array
- `selection`: `{ productId, termYears }`
- `errors`: string | null

### 7.3 Tools (Mocked)
- `calculate_ltv(propertyValue, loanBalance)`
- `fetch_mortgage_products(ltv, fixYears)`
- `recalculate_monthly_payment(principal, annualRate, termYears, fee)`

**Mock Products**:
- Barclays Standard Fix — 4.2%, £999
- Barclays Premier Fix — 4.0%, £1499

---

## 8. Latency Budgeting
### 8.1 Targets (Measured client-side)
- **TTFB**: < 500ms
- **UI loading paint**: < 400ms
- **TTS start**: < 900ms
- **Cards visible**: < 2000ms

### 8.2 Turn Strategy
On `client.audio.stop`, server must immediately emit:
1. `server.transcript.final`
2. `server.agent.thinking`
3. `server.a2ui.patch` (Loading state)
*Only then perform tool calculations.*

---

## 9. Interrupt Handling (Barge-in)
If TTS is playing and user presses push-to-talk:
1.  Client calls `speechSynthesis.cancel()`.
2.  Client sends `client.audio.interrupt`.
3.  Server cancels in-flight speech and suppresses remaining `server.voice.say` events.

---

## 10. Implementation Requirements

### 10.1 Backend
- Python FastAPI with WebSocket endpoint `/ws`
- One session per connection; clear state on disconnect
- Pydantic validation for inbound events
- Do not log raw audio bytes

### 10.2 Client
- Layout: Left (transcript + controls), Right (A2UI surface)
- Push-to-talk and Mode toggle
- Latency HUD (TTFB / UI / Voice)

---

## 11. Acceptance Criteria
- Voice → UI → slider → select → confirm works end-to-end
- 62.5% LTV appears for 400k / 250k input
- Slider updates monthly payment via `server.a2ui.patch`
- Voice responses never exceed 2 sentences
- Barge-in stops playback instantly
- loading state appears before cards; TTFB < 500ms

---

## 12. Repo Structure
- `/client`: `/src/components`, `/a2ui`, `/audio`, `/ws`
- `/server`: `main.py`, `/agent/graph.py`, `/agent/tools.py`, `requirements.txt`

---

## 13. Security
- No logging of raw audio bytes
- Max payload limits + All UI actions validated
- No arbitrary code execution or external API calls in v1
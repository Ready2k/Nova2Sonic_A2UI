# LANGGRAPH_DECISION_STATE — Barclays Mortgage Assistant

## 1. State (Single Source of Truth)
State object (canonical fields):

- `mode`: "voice" | "text"
- `transcript`: string (latest final user input)
- `intent`:
  - `propertyValue`: number | null
  - `loanBalance`: number | null
  - `fixYears`: number | null
  - `termYears`: number (default 25)
- `ltv`: number | null
- `products`: array<Product>
- `selection`:
  - `productId`: string | null
  - `termYears`: number (mirror of intent.termYears)
- `ui`:
  - `surfaceId`: "main"
  - `state`: "LOADING" | "COMPARISON" | "SUMMARY" | "CONFIRMED" | "ERROR"
- `errors`:
  - `code`: string | null
  - `message`: string | null

Product shape (mocked):
- `productId`: string
- `name`: string
- `annualRate`: number (e.g. 0.042)
- `fee`: number (GBP)

Computed per render:
- `monthlyPayment`: number
- `totalInterest`: number
- `totalPaid`: number

---

## 2. Nodes (Required) and What They Do

### Node: ingest_input
Purpose:
- Receive a new user turn (voice transcript final or client.text).
- Store it in `state.transcript`.
- Ensure `state.mode` is set.
Outputs:
- None directly; routes to `interpret_intent`.

### Node: interpret_intent
Purpose:
- Extract canonical fields from transcript:
  - propertyValue, loanBalance, fixYears, termYears (optional)
- If `termYears` missing: keep default 25.
Decision:
- If required fields missing → route to `render_missing_inputs`
- Else → route to `call_mortgage_tools`

### Node: render_missing_inputs
Purpose:
- Render UI that asks for missing fields (minimal).
- Emit short voice question (ONE at a time) if in voice mode.
Rules:
- Ask only for the highest-priority missing field:
  1) propertyValue
  2) loanBalance
  3) fixYears
Outputs:
- `server.a2ui.patch` state (UI “Please provide X”)
- Optional `server.voice.say` (max 1 sentence)
Next:
- Wait for next user turn → back to `ingest_input`

### Node: call_mortgage_tools
Purpose:
- Immediately emit LOADING UI (if not already emitted by transport layer).
- Compute:
  - LTV
  - Fetch products (2)
  - Compute monthly payment totals for each product at current termYears
Outputs:
- Sets `state.ltv`, `state.products` (with computed fields)
Next:
- `render_products_a2ui`

### Node: render_products_a2ui
Purpose:
- Emit State A (COMPARISON) A2UI patch:
  - Header summary
  - LTV gauge + band label
  - Term slider (5–40, default termYears)
  - Product cards (2)
- Emit short voice summary if voice mode (max 2 sentences).
Next:
- Wait for UI action or new transcript:
  - `handle_ui_action`

### Node: handle_ui_action
Purpose:
- Handle `client.ui.action` events:
  - update_term
  - select_product
  - confirm_application
  - reset_flow

Decision routes:

#### If actionId == update_term
- Update `intent.termYears` and `selection.termYears`
- Route → `recalculate_and_patch`

#### If actionId == select_product
- Update `selection.productId`
- Route → `render_summary_a2ui`

#### If actionId == confirm_application
- Route → `confirm_application`

#### If actionId == reset_flow
- Clear: transcript, ltv, products, selection, errors
- Reset intent to: propertyValue null, loanBalance null, fixYears null, termYears 25
- Route → `render_missing_inputs` or initial COMPARISON placeholder (your choice, document in DECISIONS.md)

### Node: recalculate_and_patch
Purpose:
- Recompute payment totals for each product using new termYears.
- Emit updated COMPARISON A2UI patch with updated monthly/interest numbers.
Performance requirement:
- In mocked mode, must emit patch within <150ms.
Next:
- Back to `handle_ui_action` (await further actions)

### Node: render_summary_a2ui
Purpose:
- Emit State B (SUMMARY) A2UI patch:
  - Selected product, term, monthly, fee, total interest
  - Disclaimer panel
  - Buttons confirm_application / reset_flow
- Optional short voice prompt:
  - “I’ve put the summary on screen. You can confirm if you want to proceed.”
Next:
- Await `confirm_application` or `reset_flow` → back to `handle_ui_action`

### Node: confirm_application
Purpose:
- Emit CONFIRMED UI patch:
  - “Application started” message (demo)
  - Reset button
- Emit one short voice confirmation (<= 1 sentence).
Next:
- Await reset_flow

### Node: error_handler (optional but recommended)
Purpose:
- Set `ui.state = ERROR`
- Emit error A2UI patch with reset option
- Short voice message if voice mode
Next:
- Await reset_flow or new input

---

## 3. Routing Rules (Decision Logic)
1) New user text/voice final → ingest_input → interpret_intent
2) If missing required fields → render_missing_inputs → wait
3) If complete → call_mortgage_tools → render_products_a2ui → wait
4) UI actions:
   - update_term → recalculate_and_patch → wait
   - select_product → render_summary_a2ui → wait
   - confirm_application → confirmed → wait
   - reset_flow → reset → missing_inputs → wait

---

## 4. Voice/UX Guards (Enforced Across Nodes)
- Any spoken output: max 2 sentences.
- Never speak raw tables, fees + rates + totals all in one go.
- If user barges in:
  - stop speaking immediately
  - prioritise listening (handled at transport/UI layer, but agent must suppress additional voice output for the interrupted turn)

---

## 5. Test Scenarios
1) Happy path (400k, 250k, 5-year fix) → LTV 62.5 → 2 products → slider update → select → confirm.
2) Missing propertyValue → asks for it (voice) + UI prompt.
3) Missing fixYears → asks for it.
4) Slider spam → must remain responsive (patch <150ms mocked).
5) Barge-in during voice → speech cancels; agent does not continue speaking.

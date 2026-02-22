# Goal-Based Test Scripts — Barclays Mortgage Assistant (FTB Focus)

These scripts are designed to validate **end-to-end goals** (not just utterance parsing).
Each scenario includes: **Goal**, **Preconditions**, **Steps (User View)**, **Expected System Behaviour**, and **Pass/Fail Checks**.

---

## GBT-FTB-01 — FTB Basic Quote (Deposit + Property Price)
**Goal:** User gets two illustrative 5-year fixed options and understands their LTV.

**Preconditions:**
- Voice mode ON
- Term default 25 years
- Mock products enabled (2 products)

**User Steps (Voice):**
1) “I’m a first time buyer. The house is about three hundred and fifty thousand and I’ve got forty grand deposit. Five year fix.”

**Expected System Behaviour:**
- Transcript captures: propertyValue=350000, deposit=40000 OR derives loanBalance=310000.
- Agent speaks (≤2 sentences): announces LTV and points user to screen.
- UI shows:
  - LTV gauge around **88.6%**
  - Term slider at 25
  - Two product cards with monthly + total interest

**Pass/Fail Checks:**
- ✅ UI shows LTV ≈ 88.6% (±0.2% acceptable if rounding)
- ✅ Two product cards render
- ✅ Voice ≤2 sentences
- ✅ No requests for income or personal data

---

## GBT-FTB-02 — Correction Handling (“sorry…”)
**Goal:** User corrects a number mid-sentence and system uses corrected value.

**Preconditions:**
- Voice mode ON

**User Steps (Voice):**
1) “We’re buying our first place. It’s four twenty… sorry, four hundred and twenty thousand. Deposit is sixty five thousand. Five year fix.”

**Expected System Behaviour:**
- Uses corrected propertyValue=420000 (not 420).
- Derives loanBalance=355000.
- LTV computed ≈ 84.5%.
- UI comparison renders.

**Pass/Fail Checks:**
- ✅ propertyValue shown as 420,000
- ✅ LTV ≈ 84.5%
- ✅ No extra clarification question unless extraction failed

---

## GBT-FTB-03 — Missing Fix Period Prompt
**Goal:** When fixYears missing, agent asks one short question and does not proceed prematurely.

**Preconditions:**
- Voice mode ON

**User Steps (Voice):**
1) “I’m buying for three hundred thousand with thirty thousand deposit.”

**Expected System Behaviour:**
- Agent asks ONE question: “Are you looking for a two-year or five-year fix?”
- UI shows missing-input prompt state (or minimal placeholder) without product cards.

**User Steps (Voice):**
2) “Five years.”

**Expected System Behaviour:**
- UI transitions to loading then comparison.
- Two product cards show.

**Pass/Fail Checks:**
- ✅ Only one question asked at a time
- ✅ No product cards until fixYears known
- ✅ After “Five years”, cards appear

---

## GBT-FTB-04 — Term Slider Recalculation (Fast Path)
**Goal:** User adjusts term and card payments update quickly without re-running intent extraction.

**Preconditions:**
- Comparison UI visible with two product cards
- Latency HUD visible

**User Steps (UI):**
1) Drag term slider from 25 → 30.

**Expected System Behaviour:**
- Client sends `client.ui.action` update_term with termYears=30.
- Server responds with `server.a2ui.patch` updating monthly and total interest.
- In mocked mode patch time <150ms.

**Pass/Fail Checks:**
- ✅ Only UI patch sent (no new “missing info” prompt)
- ✅ LTV unchanged
- ✅ Monthly changes (lower monthly at longer term)
- ✅ Patch latency <150ms (mocked)

---

## GBT-FTB-05 — Product Selection → Summary State
**Goal:** User selects a product and sees summary with disclaimer and confirm.

**Preconditions:**
- Comparison UI visible

**User Steps (UI):**
1) Tap product card “Barclays Standard Fix”.

**Expected System Behaviour:**
- UI transitions to State B (Summary):
  - Selected product details
  - Term years
  - Monthly payment
  - Fee
  - Total interest
  - Disclaimer panel
  - Confirm + Reset buttons

**Pass/Fail Checks:**
- ✅ Summary state present
- ✅ Disclaimer present
- ✅ Confirm button present

---

## GBT-FTB-06 — Confirm Application (Demo Confirm)
**Goal:** User confirms and reaches confirmed state with short voice.

**Preconditions:**
- Summary state visible

**User Steps (UI):**
1) Tap “Confirm Application”.

**Expected System Behaviour:**
- UI transitions to Confirmed state.
- Agent voice: one short sentence (≤2 sentences allowed but aim for 1).
- Reset option available.

**Pass/Fail Checks:**
- ✅ Confirmed state visible
- ✅ Voice ≤2 sentences
- ✅ Reset button visible

---

## GBT-FTB-07 — Voice-Based Product Choice (“first one”)
**Goal:** User chooses product via voice instead of tapping.

**Preconditions:**
- Comparison UI visible
- Voice mode ON

**User Steps (Voice):**
1) “I’ll go with the first one.”

**Expected System Behaviour:**
- System maps to first card productId.
- UI transitions to Summary state.

**Pass/Fail Checks:**
- ✅ Summary state reached without UI tap
- ✅ Selected product matches first card

---

## GBT-FTB-08 — Barge-In Stops Voice Immediately
**Goal:** User interrupts TTS and system stops speaking instantly, then processes new input.

**Preconditions:**
- Voice mode ON
- Agent is speaking (during comparison summary)

**User Steps:**
1) While TTS is speaking, press push-to-talk and say:
   “Wait, deposit is seventy thousand, not sixty.”

**Expected System Behaviour:**
- Client cancels TTS immediately.
- Sends `client.audio.interrupt` before audio.start.
- New transcript processed.
- UI recalculates LTV and updates.

**Pass/Fail Checks:**
- ✅ Audio stops immediately (no continued speech)
- ✅ LTV changes appropriately
- ✅ UI updates to reflect new loanBalance

---

## GBT-FTB-09 — Reset Flow Clears State
**Goal:** User resets and system clears prior intent/products/selection.

**Preconditions:**
- Any state beyond loading (Comparison/Summary/Confirmed)

**User Steps (UI):**
1) Tap “Reset”.

**Expected System Behaviour:**
- Clears intent fields (except default termYears=25).
- UI returns to missing-input prompt (or initial capture UI).
- No leftover product selection.

**Pass/Fail Checks:**
- ✅ No previous values shown
- ✅ Slider returns to 25
- ✅ No product cards until new inputs provided

---

## GBT-FTB-10 — Input Validation Error (Division by Zero / Invalid Values)
**Goal:** Invalid inputs trigger safe error UI and recoverable path.

**Preconditions:**
- Voice or text mode

**User Steps (Text):**
1) “Property value is 0 and loan is 200000. Five year fix.”

**Expected System Behaviour:**
- Agent does NOT crash.
- Sends `server.error` with `CALC_FAILED` (or equivalent).
- UI shows ERROR state with reset.

**Pass/Fail Checks:**
- ✅ Error state shown
- ✅ Recoverable reset offered
- ✅ No stack traces leaked

---

## GBT-FTB-11 — Vague User (One Question at a Time)
**Goal:** User provides vague info; agent asks for missing values sequentially.

**Preconditions:**
- Voice mode ON

**User Steps (Voice):**
1) “I don’t know what I can afford. I’ve got about thirty grand saved.”

**Expected System Behaviour:**
- Agent asks ONE question: “Roughly what price range are you looking at?”
- No multi-question overload.

**User Steps (Voice):**
2) “About three hundred thousand.”

**Expected:**
- Agent asks ONE question: “Two-year or five-year fix?”

**User Steps (Voice):**
3) “Five year.”

**Expected:**
- Render options.

**Pass/Fail Checks:**
- ✅ One question at a time
- ✅ No products until minimum fields collected
- ✅ Smooth progression

---

## GBT-FTB-12 — Performance: TTFB + Loading State
**Goal:** After end-of-speech, user sees immediate “Calculating…” UI and low TTFB.

**Preconditions:**
- Voice mode ON
- Latency HUD enabled

**User Steps (Voice):**
1) Speak: “Buying for four hundred thousand, deposit sixty thousand, five year fix.”

**Expected System Behaviour:**
- On `client.audio.stop`, server emits immediately:
  1) transcript.final
  2) agent.thinking state rendering_ui
  3) a2ui.patch Loading state
- TTFB <500ms (HUD)

**Pass/Fail Checks:**
- ✅ Loading state appears before cards
- ✅ TTFB shown <500ms in HUD (mocked)
- ✅ Cards appear <2000ms (mocked)

---

## Notes for Test Execution
- Keep a screen recording for each GBT case.
- For voice scripts, speak naturally (hesitations, pauses).
- Repeat slider tests with rapid movements to detect patch backlog/flicker.
- Use reset between scenarios unless testing state carry-over intentionally.

# Refactor steps (do in this order)

## 1) Freeze the external contract

Do not change any of these:

* WS event names
* `client.ui.action` actionIds
* canonical field names (`propertyValue`, `loanBalance`, `fixYears`, `termYears`)
* `surfaceId` `"main"`

This refactor is internal architecture only.

## 2) Update state model in `graph.py`

Add and enforce:

```python
state["ui"] = {"surfaceId": "main", "state": "LOADING|COMPARISON|SUMMARY|CONFIRMED|ERROR"}
state["errors"] = None  # or {"code":..., "message":..., "recoverable":...}
state["pendingAction"] = None  # when a UI action arrives
state["outbox"] = []  # list of server events to send (voice.say, a2ui.patch, error, etc.)
```

**Rule:** Nodes push events to `state["outbox"]`. `main.py` only forwards outbox.

## 3) Make `main.py` thin and boring

`main.py` should:

* Validate envelope + payload shape (Pydantic)
* Map inbound events into state:
  * `client.text` and `server.transcript.final`: set `state["transcript"]`
  * `client.ui.action`: set `state["pendingAction"] = {actionId, data}`
  * `client.audio.stop`: if using mock STT, generate transcript and set `state["transcript"]`
  * `client.audio.interrupt`: set a flag `state["speechInterrupted"]=True` (optional) but do not recalc anything here
* Call the graph
* Send events from `state["outbox"]`
* Clear outbox after sending

**No more:**
* resetting state in `main.py`
* recalculation in `main.py`
* “if missing variables then …” logic in `main.py`

## 4) Split rendering responsibilities properly

Right now `render_products_a2ui` is doing too much.

Create two render nodes:

* **`render_missing_inputs`**
  Renders “need property value” / “need loan balance” / “need fix years” UI and a single short voice question (if voice mode).

* **`render_products_a2ui`**
  Only renders COMPARISON state (LTV gauge, slider, 2 cards).

## 5) Move UI actions into `handle_ui_action`

`handle_ui_action` should:

* Read `state["pendingAction"]`
* Route:
  * `update_term` → update `intent.termYears` → `recalculate_and_patch`
  * `select_product` → set `selection.productId` → `render_summary_a2ui`
  * `confirm_application` → `confirm_application`
  * `reset_flow` → reset state → `render_missing_inputs` (or initial placeholder, pick one and record in `DECISIONS.md`)
* Then clear `pendingAction`.

## 6) Add the fast path node `recalculate_and_patch`

This node should do no tool fetching.
It should:

* recompute monthly/interest totals for existing `state.products` using new `termYears`
* emit one `server.a2ui.patch` updating card numbers
* set `state.ui.state = "COMPARISON"`
* ensure mocked mode hits your <150ms requirement

## 7) Add `confirm_application` node

It should:

* set `ui.state="CONFIRMED"`
* emit confirmed A2UI patch
* emit one short `server.voice.say` sentence

Stop using `voice_confirm` as a dummy. Either implement it or delete it.

## 8) Add `error_handler` (recommended)

Any node can set `state.errors` and route to `error_handler`, which:

* sets `ui.state="ERROR"`
* emits error UI + reset button
* emits short voice apology (1 sentence max) if voice mode

## Routing logic (the “blueprint truth table”)

When the graph runs, it should decide:

* If `pendingAction` exists → go `handle_ui_action`
* Else if missing required intent fields → `render_missing_inputs`
* Else → `call_mortgage_tools` → `render_products_a2ui`

That’s it. No surprise side quests.

## Acceptance tests (must still pass)

* Voice query (“400k value, 250k owe, 5-year fix”) → shows 62.5% LTV
* Slider to 30 years → payment updates via `server.a2ui.patch` quickly
* Select product → summary state with disclaimer
* Confirm → confirmed state + short voice confirmation
* Reset → returns to missing-inputs capture flow
* Barge-in still works (even if graph doesn’t handle audio streaming, it must not emit continued voice after interrupt)

## What to change first (practical sequencing)

If you want the least pain:

1. Implement outbox + pendingAction + ui state in `graph.py`
2. Make `main.py` forward-only (no state mutation beyond setting transcript/pendingAction)
3. Split `render_missing_inputs` out of `render_products_a2ui`
4. Add `recalculate_and_patch` fast path
5. Add `confirm_application`
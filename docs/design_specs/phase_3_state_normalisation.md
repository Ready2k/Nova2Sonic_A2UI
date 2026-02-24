# Phase 3 Design Spec — Normalise State Envelope

**Depends on:** Phase 1 and Phase 2 complete
**Branch suggestion:** `refactor/phase-3-state-normalisation`
**Estimated files changed:** 2 (graph.py, main.py) — iterative, commit after each field group

## Objective

Move mortgage-specific fields from the top level of `AgentState` into
`state["domain"]["mortgage"]`. This eliminates key-collision risk for future plugins and
gives every plugin a clean, predictable root state shape matching `CommonState`.

This is a **multi-step migration**. Each step is safe to deploy independently.
Run the full integration test suite after each step before continuing.

---

## Ground Rules

1. **One field group per commit.** If a step breaks tests, roll back only that commit.
2. **Dual-write during migration.** Write to both the old top-level key and the new
   `domain.mortgage` path until the shim is confirmed stable.
3. **Remove the old key last.** Only after all reads in `graph.py` and `main.py` have
   switched to the `domain.mortgage` path.
4. **Tests gate every step.** The integration suite must pass before advancing.

---

## Migration Map

| Group | Fields | Risk |
|---|---|---|
| A | `branch_requested` | Low — one node reads it |
| B | `address_validation_failed`, `last_attempted_address` | Low — one node reads each |
| C | `trouble_count`, `show_support` | Medium — `show_support` read by `main.py` |
| D | `existing_customer`, `property_seen`, `process_question` | Medium — read in intent extraction |
| E | `intent` (sub-fields), `ltv`, `products`, `selection` | High — central to all routing |

Complete Groups A → E in order. Do not skip ahead.

---

## Accessor Helpers (Add Once, Used Everywhere)

Add these two helper functions near the top of `plugins/mortgage/graph.py`, after the
existing helper block (after `_answer_process_question`, before the `AgentState` TypedDict,
approximately line 215):

```python
# ── Domain state accessors ──────────────────────────────────────────────────────
# These provide backward-compatible reads during the Phase 3 migration.
# Once all top-level fields are removed, these can be inlined or deleted.

def _dm(state: dict) -> dict:
    """Return the mortgage domain sub-dict, creating it if absent."""
    domain = state.setdefault("domain", {})
    if "mortgage" not in domain:
        domain["mortgage"] = {}
    return domain["mortgage"]


def _dm_get(state: dict, key: str, default=None):
    """
    Read a field from domain.mortgage with fallback to top-level key.
    Supports the dual-write window where both paths may exist.
    """
    dm = state.get("domain", {}).get("mortgage", {})
    if key in dm:
        return dm[key]
    return state.get(key, default)
```

These two helpers are the **only** new code. All field migrations below use them.

---

## Group A — `branch_requested`

### A.1 Update `create_initial_state` in `plugins/mortgage/plugin.py`

Add `branch_requested` to the `domain.mortgage` sub-dict:

**Before** (in `create_initial_state`, the `domain` key):
```python
            "domain": {},
```

**After:**
```python
            "domain": {
                "mortgage": {
                    "branch_requested": False,
                },
            },
```

Keep the top-level `"branch_requested": False` key in place for now (dual-write).

### A.2 Update writes in `graph.py`

Search for every assignment to `branch_requested` in `graph.py`. There is one site in
`interpret_intent` (the node that sets `branch_requested = True`):

**Find the return statement in `interpret_intent` that sets branch_requested.**
It will look like:
```python
    return {
        ...
        "branch_requested": branch_data.get("requested", False),
        ...
    }
```

**Add the domain write alongside the existing top-level write:**
```python
    domain_update = _dm(state)
    domain_update["branch_requested"] = branch_data.get("requested", False)

    return {
        ...
        "branch_requested": branch_data.get("requested", False),  # keep during dual-write
        "domain": state.get("domain", {}),                         # propagate domain dict
        ...
    }
```

### A.3 Update reads in `graph.py`

Find every `state.get("branch_requested")` or `state["branch_requested"]` and replace with:
```python
_dm_get(state, "branch_requested", False)
```

### A.4 Test

```bash
python -m pytest server/tests/test_math.py -v
cd tests && python run_tests.py  # requires server running
```

### A.5 Clean up (after tests pass)

Remove `"branch_requested": False` from the top-level keys in `create_initial_state`.
Remove `"branch_requested": branch_data.get(...)` from the `return` dict in `interpret_intent`.

---

## Group B — `address_validation_failed`, `last_attempted_address`

### B.1 Update `create_initial_state`

```python
            "domain": {
                "mortgage": {
                    "branch_requested": False,         # from Group A
                    "address_validation_failed": False,
                    "last_attempted_address": None,
                },
            },
```

Remove top-level keys after dual-write confirmed.

### B.2 Update writes in `graph.py`

These are set in `interpret_intent` when address validation fails.
Apply the same dual-write pattern as Group A.

### B.3 Update reads in `graph.py`

Find `state.get("address_validation_failed")` and `state.get("last_attempted_address")`.
Replace with `_dm_get(state, "address_validation_failed", False)` and
`_dm_get(state, "last_attempted_address")`.

### B.4 Test → Clean up

---

## Group C — `trouble_count`, `show_support`

`show_support` is the most important field here because `main.py:process_outbox` reads it
directly (line 145 in the original). The shim in `main.py` must be in place **before**
removing the top-level key.

### C.1 Add shim to `process_outbox` in `main.py`

**Find this line in `process_outbox` (approx line 145):**
```python
                    payload["showSupport"] = state.get("show_support", False)
```

**Replace with:**
```python
                    payload["showSupport"] = (
                        state.get("show_support")
                        or state.get("domain", {}).get("mortgage", {}).get("show_support", False)
                    )
```

This shim accepts both paths. It must be committed and deployed before removing the
top-level `show_support` key.

### C.2 Update `create_initial_state`

```python
            "domain": {
                "mortgage": {
                    "branch_requested": False,
                    "address_validation_failed": False,
                    "last_attempted_address": None,
                    "trouble_count": 0,
                    "show_support": False,
                },
            },
```

### C.3 Update writes in `graph.py`

`trouble_count` is incremented in `interpret_intent`.
`show_support` is set to `True` when `trouble_count >= 2`.

Apply dual-write: update both `state["trouble_count"]` / `state["show_support"]` and
`_dm(state)["trouble_count"]` / `_dm(state)["show_support"]` simultaneously.

### C.4 Update reads in `graph.py`

Replace `state.get("trouble_count", 0)` with `_dm_get(state, "trouble_count", 0)`.
Replace `state.get("show_support", False)` with `_dm_get(state, "show_support", False)`.

### C.5 Test → Clean up top-level keys

After all tests pass, remove top-level `trouble_count` and `show_support` from
`create_initial_state` and all return dicts. The `main.py` shim will then always
read from the `domain.mortgage` path.

---

## Group D — `existing_customer`, `property_seen`, `process_question`

These are read in multiple nodes but follow the same pattern.

### D.1 Update `create_initial_state`

```python
            "domain": {
                "mortgage": {
                    "branch_requested": False,
                    "address_validation_failed": False,
                    "last_attempted_address": None,
                    "trouble_count": 0,
                    "show_support": False,
                    "existing_customer": None,
                    "property_seen": None,
                    "process_question": None,
                },
            },
```

### D.2 Update writes and reads

Same dual-write pattern. The `_all_required_fields_present()` function and
`render_missing_inputs` both check `existing_customer` and `property_seen` — update those
reads with `_dm_get`.

### D.3 `AgentState` TypedDict — remove migrated fields

After Group D tests pass, remove these from the `AgentState` TypedDict definition:

```python
# Remove:
    existing_customer: Optional[bool]
    property_seen: Optional[bool]
    process_question: Optional[str]
```

---

## Group E — `intent`, `ltv`, `products`, `selection`

This is the highest-risk group. `intent` is read and written by nearly every node.
Do this last, in a separate PR, with a full regression test run.

### E.1 Strategy

`intent` is a nested dict rather than a scalar. The migration pattern is slightly
different: move the entire `intent` dict under `domain.mortgage.intent`.

### E.2 Update `create_initial_state`

```python
            "domain": {
                "mortgage": {
                    # ... all Group A–D fields ...
                    "intent": {
                        "propertyValue": None,
                        "loanBalance": None,
                        "fixYears": None,
                        "termYears": 25,
                    },
                    "ltv": 0.0,
                    "products": [],
                    "selection": {},
                },
            },
```

Keep the top-level `intent`, `ltv`, `products`, `selection` keys during dual-write.

### E.3 Add intent accessor helper to `graph.py`

```python
def _intent(state: dict) -> dict:
    """
    Return the intent dict, reading from domain.mortgage.intent with
    fallback to top-level state['intent'].
    """
    dm = state.get("domain", {}).get("mortgage", {})
    if "intent" in dm:
        return dm["intent"]
    return state.get("intent", {})
```

Replace all `state.get("intent", {})` with `_intent(state)`.
Replace all `intent = state["intent"]` with `intent = _intent(state)`.

### E.4 Update `MortgageIntent` merge logic

In `interpret_intent`, where extracted fields are merged back into state:

**Before (typical pattern):**
```python
    return {
        "intent": {**current_intent, **extracted_fields},
        ...
    }
```

**After (dual-write):**
```python
    merged_intent = {**_intent(state), **extracted_fields}
    dm_update = _dm(state)
    dm_update["intent"] = merged_intent

    return {
        "intent": merged_intent,      # keep top-level during dual-write
        "domain": state.get("domain", {}),
        ...
    }
```

### E.5 Update `ltv`, `products`, `selection`

Same pattern: write to both `domain.mortgage.*` and top-level during dual-write.
Reads in `render_products_a2ui`, `recalculate_and_patch`, `render_summary_a2ui` all
use `state.get("ltv", 0)` etc. — replace with `_dm_get(state, "ltv", 0)`.

### E.6 Clean up `AgentState` TypedDict

After all reads migrated and tests pass, remove from `AgentState`:
```python
# Remove:
    intent: Dict[str, Any]
    ltv: float
    products: List[Dict[str, Any]]
    selection: Dict[str, Any]
```

And remove the remaining top-level keys from `create_initial_state`.

---

## Final State of `AgentState` after Group E

```python
class AgentState(TypedDict):
    # ── CommonState envelope (shared with all plugins) ──────────────────
    mode: str
    device: str
    transcript: str
    messages: Annotated[List[Dict[str, Any]], append_reducer]
    ui: Dict[str, Any]
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]
    outbox: Annotated[List[Dict[str, Any]], append_reducer]
    meta: Dict[str, Any]
    domain: Dict[str, Any]
    state_version: int
```

All mortgage-specific data lives in `state["domain"]["mortgage"]` as a plain dict.
The `MortgageIntent` Pydantic model remains in `graph.py` for LLM structured output —
it is not part of `AgentState`.

---

## Final State of `process_outbox` in `main.py`

After Group C cleanup, `main.py` always reads `show_support` from the domain path:

```python
                    payload["showSupport"] = (
                        state.get("domain", {})
                        .get("mortgage", {})
                        .get("show_support", False)
                    )
```

The `or state.get("show_support")` fallback can be removed once Group C cleanup is complete.

---

## Integration Test Checkpoints

After each group completes:

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# Unit tests
python -m pytest server/tests/test_math.py -v

# Import check
python -c "from app.agent.plugins.mortgage.graph import app_graph; print('OK')"

# Graph invoke check on initial state
python -c "
from app.agent.plugins.mortgage.plugin import MortgagePlugin
p = MortgagePlugin()
s = p.create_initial_state()
g = p.build_graph()
result = g.invoke(s)
assert len(result.get('outbox', [])) > 0, 'No outbox events on initial render'
print('Graph invoke on initial state: OK')
"

# Integration tests (requires server running on :8000)
cd tests && python run_tests.py
```

---

## Acceptance Criteria (Definition of Done for Group E — full migration complete)

- [ ] `AgentState` TypedDict contains only `CommonState` envelope keys
- [ ] All mortgage domain data accessed via `state["domain"]["mortgage"]`
- [ ] `main.py` reads `show_support` exclusively from `domain.mortgage` path
- [ ] All five existing integration test scenarios pass
- [ ] `python -m pytest server/tests/test_math.py -v` passes
- [ ] No `state.get("intent")`, `state.get("ltv")`, `state.get("products")`,
  `state.get("trouble_count")`, `state.get("show_support")`, `state.get("existing_customer")`,
  `state.get("property_seen")`, `state.get("branch_requested")`,
  `state.get("address_validation_failed")` calls remain in `graph.py`

---

## Rollback Per Group

Each group is a separate commit. To roll back a group:

```bash
git revert HEAD  # reverts only the most recent commit
```

Because each group uses dual-write, reverting does not break in-flight sessions —
they continue reading from the top-level key that was never fully removed until cleanup.

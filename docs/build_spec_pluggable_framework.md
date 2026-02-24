# Build Spec: Pluggable LangGraph Agent Framework

**Status:** Ready to implement
**Source:** `docs/pluggable_agent_framework.md`
**Goal:** Transform the mortgage-specific app into a multi-domain agent platform without a big-bang rewrite, and without breaking the current runtime or client.

---

## Closed Decisions

These questions from the design doc are answered here and treated as fixed constraints for implementation.

| Decision | Choice | Rationale |
|---|---|---|
| Plugin selection rule | Per-session, via WS query param `?agent=<id>`, default `mortgage` | Zero infrastructure change; each session is independent |
| State versioning | `state_version: int` in `CommonState`; each plugin declares its schema version | Simple; migrations run at plugin `create_initial_state` boundary |
| Action namespace | Strict prefix enforcement: `<plugin_id>.<action>` in registry adapter; legacy bare IDs accepted with deprecation log | Avoids collisions; backward-compatible |
| Shared vs plugin UI | Shared = existing A2UI component set (`A2Renderer.tsx`). Plugins may declare new component types; renderer shows red error box for unknown types (existing behavior, acceptable) | No renderer changes required in Phase 1–3 |
| Cross-domain handoff | Out of scope for this spec. Document design note only. | Deferred; add `handoff_to` outbox event type in a future phase |

---

## Target Folder Structure

```
server/app/agent/
  core/
    __init__.py
    contracts.py          # PluginBase ABC + CommonState TypedDict + ServerEvent types
    registry.py           # AgentRegistry: discover + select plugin by agent_id
    runtime_adapter.py    # Thin wrapper: graph.invoke + state transitions used by main.py
  plugins/
    __init__.py
    mortgage/
      __init__.py
      plugin.py           # MortgagePlugin(PluginBase)
      graph.py            # ← existing graph.py moved here (unchanged)
      tools.py            # ← existing tools.py moved here (unchanged)
    lost_card/            # Phase 4 — scaffold only in Phase 1
      __init__.py
      plugin.py
      graph.py
      tools.py
```

Existing paths that move:
- `server/app/agent/graph.py` → `server/app/agent/plugins/mortgage/graph.py`
- `server/app/agent/tools.py` → `server/app/agent/plugins/mortgage/tools.py`

---

## Phase 1 — Isolate Mortgage Graph Behind Plugin Wrapper

**Goal:** `main.py` no longer imports `app_graph` or `AgentState` directly. Behaviour unchanged.

### 1.1 Create `core/contracts.py`

```python
# server/app/agent/core/contracts.py

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypedDict, Annotated
import operator


# ── Common State ────────────────────────────────────────────────────────────
# Every plugin state must be a dict that satisfies this envelope.
# Domain-specific fields live under state["domain"][plugin_id].

class CommonState(TypedDict, total=False):
    mode: str                          # "voice" | "text"
    device: str                        # "desktop" | "mobile"
    transcript: str
    messages: List[Dict[str, Any]]     # append-reduced in graph
    ui: Dict[str, Any]                 # { surfaceId, state }
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]
    outbox: List[Dict[str, Any]]       # append-reduced in graph
    meta: Dict[str, Any]               # session metadata (session_id, agent_id, etc.)
    domain: Dict[str, Any]             # plugin-owned payload — keyed by plugin_id
    state_version: int


# ── Server Event shape (outbox entries) ────────────────────────────────────
class ServerEvent(TypedDict):
    type: str          # e.g. "server.a2ui.patch", "server.voice.say"
    payload: Dict[str, Any]


# ── Plugin Interface ────────────────────────────────────────────────────────
class PluginBase(ABC):

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique identifier, e.g. 'mortgage', 'lost_card'."""
        ...

    @property
    def state_version(self) -> int:
        """Schema version for this plugin's domain state."""
        return 1

    @abstractmethod
    def create_initial_state(self) -> Dict[str, Any]:
        """
        Return a fully initialised agent state dict.
        Must include all CommonState keys plus any plugin-specific root keys
        needed for backward compatibility during Phase 1–2.
        """
        ...

    @abstractmethod
    def build_graph(self):
        """Return a compiled LangGraph CompiledStateGraph."""
        ...

    def validate_action(self, action_id: str, data: Dict[str, Any]) -> Optional[str]:
        """
        Validate and normalise an incoming UI action ID.
        Returns the canonical action_id string, or None to reject.
        Default: accept any action prefixed with plugin_id or bare legacy IDs.
        """
        return action_id

    @property
    def capabilities(self) -> Dict[str, Any]:
        """Optional metadata: voice_prompt, supported_components, etc."""
        return {}
```

**Acceptance criteria:**
- `from app.agent.core.contracts import PluginBase, CommonState, ServerEvent` succeeds with no imports of mortgage code.

---

### 1.2 Create `plugins/mortgage/plugin.py`

```python
# server/app/agent/plugins/mortgage/plugin.py

from app.agent.core.contracts import PluginBase
from .graph import app_graph     # the existing compiled graph
from typing import Dict, Any


class MortgagePlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return "mortgage"

    def create_initial_state(self) -> Dict[str, Any]:
        # Identical to the current create_initial_state() in main.py.
        # Copied here so main.py can delegate to the plugin.
        return {
            "mode": "text",
            "device": "desktop",
            "transcript": "",
            "messages": [],
            "intent": {
                "propertyValue": None, "loanBalance": None,
                "fixYears": None, "termYears": 25,
            },
            "ltv": 0.0,
            "products": [],
            "selection": {},
            "ui": {"surfaceId": "main", "state": "LOADING"},
            "errors": None,
            "pendingAction": None,
            "outbox": [],
            "existing_customer": None,
            "property_seen": None,
            "trouble_count": 0,
            "show_support": False,
            "address_validation_failed": False,
            "last_attempted_address": None,
            "branch_requested": False,
            "process_question": None,
            # CommonState envelope fields
            "meta": {},
            "domain": {},
            "state_version": 1,
        }

    def build_graph(self):
        return app_graph
```

**File moves required** (rename, no content changes):
- `server/app/agent/graph.py` → `server/app/agent/plugins/mortgage/graph.py`
- `server/app/agent/tools.py` → `server/app/agent/plugins/mortgage/tools.py`

Update the import in `plugins/mortgage/graph.py` line 12:
```python
# Before:
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment
# After (unchanged — relative import still correct from new location):
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment
```

---

### 1.3 Create `core/registry.py`

```python
# server/app/agent/core/registry.py

import logging
from typing import Dict
from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)

_plugins: Dict[str, PluginBase] = {}


def register(plugin: PluginBase) -> None:
    _plugins[plugin.plugin_id] = plugin
    logger.info(f"[AgentRegistry] Registered plugin: {plugin.plugin_id}")


def get_plugin(agent_id: str) -> PluginBase:
    plugin = _plugins.get(agent_id)
    if plugin is None:
        available = list(_plugins.keys())
        raise KeyError(f"No plugin registered for agent_id='{agent_id}'. Available: {available}")
    return plugin


def get_default() -> PluginBase:
    return get_plugin("mortgage")
```

Add plugin registration at app startup (in `main.py` or a new `server/app/agent/__init__.py`):
```python
from app.agent.core.registry import register
from app.agent.plugins.mortgage.plugin import MortgagePlugin
register(MortgagePlugin())
```

---

### 1.4 Create `core/runtime_adapter.py`

```python
# server/app/agent/core/runtime_adapter.py

import asyncio
import logging
from typing import Dict, Any
from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)


async def invoke_graph(plugin: PluginBase, state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the plugin's compiled graph on a thread so the event loop is not blocked."""
    graph = plugin.build_graph()
    return await asyncio.to_thread(graph.invoke, state, config)
```

---

### 1.5 Update `main.py`

**Changes only — minimal surgical edits:**

```python
# Remove:
from .agent.graph import app_graph, AgentState

# Add:
from .agent.core.registry import get_plugin
from .agent.core.runtime_adapter import invoke_graph
from .agent.plugins.mortgage.plugin import MortgagePlugin  # triggers registration
from .agent.core.registry import register
register(MortgagePlugin())
```

Change `create_initial_state()` (currently lines 38–60) to:
```python
def create_initial_state(agent_id: str = "mortgage") -> dict:
    plugin = get_plugin(agent_id)
    return plugin.create_initial_state()
```

Change every `await asyncio.to_thread(app_graph.invoke, state, config)` call (there are 4 — lines ~237, 317, 416, 443) to:
```python
plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
res = await invoke_graph(plugin, state, config)
```

Change `client.mode.update` device-change handler (lines 470–484) which directly imports graph functions to:
```python
plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
# Dispatch re-render through graph invoke (let the graph decide)
res = await invoke_graph(plugin, state, config)
sessions[sid]["state"] = res
await process_outbox(websocket, sid)
```
(Remove the direct import of `render_missing_inputs`, `render_products_a2ui` from graph.)

Session creation — add `agent_id` to session dict:
```python
sessions[session_id] = {
    "agent_id": agent_id,          # NEW
    "state": create_initial_state(agent_id),
    "voice_playing": False,
    ...
}
```

Parse `agent_id` from query param at WS accept:
```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, agent: str = "mortgage"):
    agent_id = agent   # FastAPI reads ?agent= automatically
```

**Acceptance criteria:**
- `./manage.sh start` starts cleanly.
- All existing mortgage flows work identically.
- `pytest tests/test_math.py -v` passes.
- `main.py` has zero direct imports from `plugins/mortgage/graph.py`.

---

## Phase 2 — Agent Registry Selection

**Goal:** Demonstrate that two plugins can coexist; the runtime selects by `?agent=` param.

### 2.1 Scaffold `plugins/lost_card/`

Create stub files only — no real logic yet:

```python
# server/app/agent/plugins/lost_card/plugin.py

from app.agent.core.contracts import PluginBase
from typing import Dict, Any


class LostCardPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return "lost_card"

    def create_initial_state(self) -> Dict[str, Any]:
        return {
            "mode": "text",
            "device": "desktop",
            "transcript": "",
            "messages": [],
            "ui": {"surfaceId": "main", "state": "LOADING"},
            "errors": None,
            "pendingAction": None,
            "outbox": [],
            "meta": {},
            "domain": {"lost_card": {}},
            "state_version": 1,
        }

    def build_graph(self):
        from .graph import app_graph
        return app_graph
```

```python
# server/app/agent/plugins/lost_card/graph.py — stub

from langgraph.graph import StateGraph, START, END
from typing import TypedDict, List, Dict, Any, Annotated
import operator


class LostCardState(TypedDict):
    mode: str
    device: str
    transcript: str
    messages: Annotated[List[Dict[str, Any]], operator.add]
    ui: Dict[str, Any]
    errors: Any
    pendingAction: Any
    outbox: Annotated[List[Dict[str, Any]], operator.add]
    meta: Dict[str, Any]
    domain: Dict[str, Any]
    state_version: int


def _placeholder_node(state):
    return {"outbox": [{"type": "server.voice.say", "payload": {"text": "Lost Card Agent coming soon."}}]}


builder = StateGraph(LostCardState)
builder.add_node("placeholder", _placeholder_node)
builder.add_edge(START, "placeholder")
builder.add_edge("placeholder", END)
app_graph = builder.compile()
```

### 2.2 Register `LostCardPlugin` at startup

```python
# in main.py plugin registration block:
from .agent.plugins.lost_card.plugin import LostCardPlugin
register(LostCardPlugin())
```

**Acceptance criteria:**
- `ws://localhost:8000/ws?agent=mortgage` → mortgage flow unchanged.
- `ws://localhost:8000/ws?agent=lost_card` → client connects, receives "Lost Card Agent coming soon." voice/text response.
- `ws://localhost:8000/ws` (no param) → defaults to mortgage.
- Unknown `?agent=unknown` → server logs error + closes WS with code 4000.

---

## Phase 3 — Normalise State Envelope

**Goal:** Mortgage domain fields begin migrating under `state["domain"]["mortgage"]`. Backward-compat shims maintained throughout.

### 3.1 Approach

This is a **gradual, field-by-field migration**. Do not move all fields at once.

Migrate in this order (lowest risk first):
1. `branch_requested` → `state["domain"]["mortgage"]["branch_requested"]`
2. `address_validation_failed`, `last_attempted_address`
3. `trouble_count`, `show_support`
4. `existing_customer`, `property_seen`, `process_question`
5. `intent` → `state["domain"]["mortgage"]["intent"]` (highest risk, do last)

For each field:
- Add to `state["domain"]["mortgage"]` in `create_initial_state`.
- Add shim in graph: `branch_requested = state.get("branch_requested") or state["domain"]["mortgage"].get("branch_requested", False)`
- After shim proves stable (all tests pass), remove the top-level key.

### 3.2 `process_outbox` shim

`process_outbox` reads `state.get("show_support", False)` directly (line 145 in main.py). Add:
```python
show_support = state.get("show_support") or state.get("domain", {}).get("mortgage", {}).get("show_support", False)
```

**Acceptance criteria:**
- All integration test scenarios pass at each migration step.
- After all fields migrated: `AgentState` in `plugins/mortgage/graph.py` references `domain.mortgage.*` for all domain fields.
- `main.py` reads `show_support` only from `domain.mortgage` path.

---

## Phase 4 — Lost Card Agent (Full Implementation)

**Goal:** A second fully functional domain plugin, proving the framework is extensible.

### 4.1 Domain State

```python
class LostCardDomain(TypedDict):
    card_status: str          # "active" | "frozen" | "cancelled"
    card_last4: Optional[str]
    freeze_confirmed: bool
    replacement_requested: bool
    replacement_eta: Optional[str]
    identity_verified: bool
    risk_level: str           # "low" | "medium" | "high"
    suspicious_tx: List[Dict]
    branch_requested: bool
    escalation_required: bool
```

### 4.2 Graph Nodes

| Node | Trigger | Output |
|---|---|---|
| `ingest_input` | All inputs | Routes via `start_router` |
| `classify_risk` | New transcript | Sets `risk_level`, increments `trouble_count` if no data |
| `freeze_card` | `lost_card.freeze_card` action | Calls freeze tool, updates `card_status` |
| `verify_identity_stepup` | `risk_level == "high"` | Renders identity challenge UI |
| `issue_replacement` | `lost_card.order_replacement` action | Calls replacement tool |
| `render_support_options` | Low-info transcript | Renders card status + options UI |
| `confirm_action` | Irreversible actions | Requires explicit confirmation before proceeding |
| `clear_pending_action` | Final node | Always runs before END |

### 4.3 Action Namespace

All Lost Card actions prefixed `lost_card.*`:
- `lost_card.freeze_card`
- `lost_card.order_replacement`
- `lost_card.report_fraud`
- `lost_card.view_transactions`
- `lost_card.find_branch`
- `lost_card.reset`

### 4.4 Tools (`plugins/lost_card/tools.py`)

Stub implementations (simulate API calls):
- `freeze_card(card_last4) -> dict` — idempotent
- `request_replacement(card_last4, delivery_address) -> dict`
- `get_recent_transactions(card_last4) -> list`

All tools are idempotent. Irreversible actions (`freeze_card`, `request_replacement`) require `state["domain"]["lost_card"]["freeze_confirmed"] == True`.

### 4.5 Security & Policy Controls

- PII redaction: strip card numbers from logs. Add `_redact_pii(text)` util.
- Audit events: every critical action emits `{"type": "server.audit.event", "payload": {"action": ..., "ts": ...}}` to outbox. `process_outbox` in main.py silently drops audit events (not forwarded to client); a future audit hook can intercept.
- Strong auth gate: `verify_identity_stepup` node blocks irreversible actions until `identity_verified == True`. In the stub, identity is verified by asking for card last 4 digits.

### 4.6 UI Components

Reuse existing A2UI types where possible:
- Card status panel → `DataCard` component
- Safety checklist → `Timeline` component
- Replacement ETA → `DataCard`
- Freeze confirmation → `Button` (confirm) + `Button` (cancel)

No new A2UI component types required for MVP.

**Acceptance criteria:**
- Happy path: user says "I've lost my card" → freeze confirmation → frozen → replacement initiated.
- Fraud path: user says "I see transactions I don't recognise" → `risk_level=high` → identity challenge → escalate.
- Reset path: user says "I found my card" after freeze → offer to unfreeze (stub: log action).
- All actions under `lost_card.*` namespace; no collisions with mortgage actions.

---

## Phase 5 — Shared Observability + Contract Tests

### 5.1 Plugin Contract Test

Create `tests/test_plugin_contract.py`. Every registered plugin must pass:

```python
import pytest
from app.agent.core.registry import get_plugin

@pytest.mark.parametrize("agent_id", ["mortgage", "lost_card"])
def test_plugin_has_plugin_id(agent_id):
    p = get_plugin(agent_id)
    assert isinstance(p.plugin_id, str) and len(p.plugin_id) > 0

@pytest.mark.parametrize("agent_id", ["mortgage", "lost_card"])
def test_plugin_creates_valid_initial_state(agent_id):
    p = get_plugin(agent_id)
    s = p.create_initial_state()
    required_keys = ["mode", "device", "transcript", "messages", "ui", "pendingAction", "outbox", "meta", "domain", "state_version"]
    for k in required_keys:
        assert k in s, f"Missing key '{k}' in {agent_id} initial state"

@pytest.mark.parametrize("agent_id", ["mortgage", "lost_card"])
def test_plugin_graph_invokes_without_error(agent_id):
    p = get_plugin(agent_id)
    graph = p.build_graph()
    state = p.create_initial_state()
    result = graph.invoke(state)
    assert "outbox" in result
```

### 5.2 Scenario Tests

Each domain has its own scenario file:
- `tests/scenarios_mortgage.py` — existing scenarios moved here
- `tests/scenarios_lost_card.py` — new scenarios (see Phase 4.5)

`run_tests.py` discovers both via prefix convention.

### 5.3 Langfuse Tracing

Existing Langfuse integration already passes `session_id` via metadata. Add `agent_id` to metadata:
```python
config = {
    "callbacks": [lf_callback],
    "metadata": {
        "langfuse_session_id": sid,
        "agent_id": sessions[sid].get("agent_id", "mortgage"),  # NEW
    }
}
```

This enables per-plugin filtering in the Langfuse dashboard with no SDK changes.

---

## Implementation Order & Dependencies

```
Phase 1 (no behaviour change)
  ├── 1.1 contracts.py
  ├── 1.2 mortgage/plugin.py  + move graph.py + tools.py
  ├── 1.3 registry.py
  ├── 1.4 runtime_adapter.py
  └── 1.5 main.py edits

Phase 2 (additive — stub plugin)
  ├── 2.1 lost_card/ scaffold
  └── 2.2 register LostCardPlugin

Phase 3 (gradual, field-by-field)
  └── state envelope migration (one field at a time, tests after each)

Phase 4 (net-new feature)
  ├── LostCard domain state + graph nodes + tools
  ├── Security controls
  └── UI mappings

Phase 5 (quality gate)
  ├── plugin contract tests
  ├── per-domain scenario tests
  └── Langfuse agent_id metadata
```

---

## Compatibility Guarantees (Unchanged Throughout)

- `WebSocketMessage` shape (`type`, `ts`, `sessionId`, `payload`) — no changes.
- Outbox event types consumed by client: `server.a2ui.patch`, `server.voice.say`, `server.voice.start`, `server.voice.stop`, `server.voice.audio`, `server.transcript.partial`, `server.transcript.final`, `server.agent.thinking` — no changes.
- Outbox processing logic in `process_outbox` (voice merge, TTS gating, thinking state) — no changes through Phase 3.
- Client (`useMortgageSocket.ts`, `A2Renderer.tsx`) — no changes required for Phase 1–3.

---

## Files Created / Modified Summary

| Action | Path |
|---|---|
| CREATE | `server/app/agent/core/__init__.py` |
| CREATE | `server/app/agent/core/contracts.py` |
| CREATE | `server/app/agent/core/registry.py` |
| CREATE | `server/app/agent/core/runtime_adapter.py` |
| CREATE | `server/app/agent/plugins/__init__.py` |
| CREATE | `server/app/agent/plugins/mortgage/__init__.py` |
| CREATE | `server/app/agent/plugins/mortgage/plugin.py` |
| MOVE   | `server/app/agent/graph.py` → `plugins/mortgage/graph.py` |
| MOVE   | `server/app/agent/tools.py` → `plugins/mortgage/tools.py` |
| CREATE | `server/app/agent/plugins/lost_card/__init__.py` |
| CREATE | `server/app/agent/plugins/lost_card/plugin.py` |
| CREATE | `server/app/agent/plugins/lost_card/graph.py` |
| CREATE | `server/app/agent/plugins/lost_card/tools.py` |
| MODIFY | `server/app/main.py` (Phase 1.5 changes only) |
| CREATE | `tests/test_plugin_contract.py` |
| CREATE | `tests/scenarios_lost_card.py` |

---

## Risk Notes

- **Phase 1 only risk:** The `client.mode.update` path in `main.py` (lines 470–484) directly imports `render_missing_inputs` and `render_products_a2ui` from the mortgage graph. These are private rendering functions. The Phase 1.5 change replaces this with a full graph `invoke`, which is slightly slower but correct. Verify the device-change UX is preserved in manual testing.
- **`messages` reducer:** Both `AgentState` and the stub `LostCardState` use `operator.add` (append) for `messages` and `outbox`. If a plugin forgets this, state will be overwritten rather than appended. The contract test catches this via the `outbox` assertion.
- **File move + import:** After moving `graph.py` and `tools.py`, run the import check: `source server/.venv/bin/activate && python -c "from app.agent.plugins.mortgage.graph import app_graph"` before any other testing.

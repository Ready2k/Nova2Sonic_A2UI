# Phase 1 Design Spec — Isolate Mortgage Graph Behind Plugin Wrapper

**Depends on:** Nothing (first phase)
**Branch suggestion:** `refactor/phase-1-plugin-isolation`
**Estimated files changed:** 7 created, 2 moved, 1 modified

## Objective

Decouple `main.py` from `graph.py` so the WebSocket runtime has no direct knowledge of
mortgage-specific symbols (`app_graph`, `AgentState`, `render_missing_inputs`,
`render_products_a2ui`). Runtime behaviour is identical after this change. No client changes.

---

## Pre-flight Check

Run these before touching any code. All must pass.

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# 1. Import check — current wiring works
python -c "from app.agent.graph import app_graph, AgentState; print('OK')"

# 2. Unit tests green
python -m pytest server/tests/test_math.py -v

# 3. Server starts (Ctrl-C after "Application startup complete")
cd server && uvicorn app.main:app --port 8000
```

---

## Step 1 — Create the `core/` package

### 1a. `server/app/agent/core/__init__.py`

Create empty file.

```python
```

### 1b. `server/app/agent/core/contracts.py`

**Full file content:**

```python
"""
contracts.py — Shared plugin interface and common state/event types.

Every domain plugin must implement PluginBase. The CommonState TypedDict
documents the envelope keys that the runtime (main.py, process_outbox) reads.
Domain-specific data lives under state["domain"][plugin_id].
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypedDict


# ── Common State ────────────────────────────────────────────────────────────────
#
# Phase 1–2: plugins keep mortgage-specific top-level keys for backward compat.
# Phase 3: those keys migrate under state["domain"]["mortgage"].
# Only the keys below are read by runtime code (main.py / process_outbox).

class CommonState(TypedDict, total=False):
    mode: str                           # "voice" | "text"
    device: str                         # "desktop" | "mobile"
    transcript: str                     # current utterance being processed
    messages: List[Dict[str, Any]]      # conversation history (append-reduced)
    ui: Dict[str, Any]                  # { surfaceId: str, state: str }
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]   # { id: str, data: dict }
    outbox: List[Dict[str, Any]]        # server events to flush (append-reduced)
    meta: Dict[str, Any]               # { session_id, agent_id, ... }
    domain: Dict[str, Any]             # plugin-owned payload keyed by plugin_id
    state_version: int                  # plugin schema version


# ── Server Event ─────────────────────────────────────────────────────────────────
#
# Shape of entries in state["outbox"]. Runtime reads only "type" and "payload".

class ServerEvent(TypedDict):
    type: str          # "server.a2ui.patch" | "server.voice.say" | etc.
    payload: Dict[str, Any]


# ── Plugin Interface ──────────────────────────────────────────────────────────────

class PluginBase(ABC):
    """
    Abstract base for all domain plugins.

    Implement plugin_id, create_initial_state, and build_graph.
    validate_action and capabilities have sensible defaults.
    """

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique slug, e.g. 'mortgage' or 'lost_card'."""
        ...

    @property
    def state_version(self) -> int:
        """Increment when the plugin's domain state shape changes."""
        return 1

    @abstractmethod
    def create_initial_state(self) -> Dict[str, Any]:
        """
        Return a fully-initialised state dict for a new session.

        Must include every key in CommonState plus any plugin-specific
        top-level keys needed during the Phase 1–2 compatibility window.
        """
        ...

    @abstractmethod
    def build_graph(self):
        """
        Return the plugin's compiled LangGraph CompiledStateGraph.

        Called once per graph invocation; implementations may cache.
        """
        ...

    def validate_action(
        self,
        action_id: str,
        data: Dict[str, Any],
    ) -> Optional[str]:
        """
        Validate and normalise a UI action ID arriving from the client.

        Returns the canonical action_id to use, or None to reject the action.
        Default implementation accepts everything.

        Phase 2+: override to enforce 'plugin_id.*' namespace.
        """
        return action_id

    @property
    def capabilities(self) -> Dict[str, Any]:
        """
        Optional metadata about what this plugin supports.

        Example keys: voice_greeting, supported_components, required_env_vars.
        Not used by the runtime; intended for tooling and documentation.
        """
        return {}
```

### 1c. `server/app/agent/core/registry.py`

**Full file content:**

```python
"""
registry.py — Plugin registration and lookup.

Plugins register themselves at import time. The runtime calls get_plugin()
with an agent_id string to retrieve the correct plugin for a session.
"""

from __future__ import annotations

import logging
from typing import Dict

from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)

# Module-level registry — populated by register() calls in main.py startup.
_registry: Dict[str, PluginBase] = {}


def register(plugin: PluginBase) -> None:
    """Register a plugin instance. Overwrites any existing entry for the same plugin_id."""
    _registry[plugin.plugin_id] = plugin
    logger.info("[AgentRegistry] Registered plugin: %s (state_version=%d)",
                plugin.plugin_id, plugin.state_version)


def get_plugin(agent_id: str) -> PluginBase:
    """
    Return the registered plugin for agent_id.

    Raises KeyError if agent_id is not registered, so callers can catch
    and close the WebSocket with a 4000 code (see main.py).
    """
    plugin = _registry.get(agent_id)
    if plugin is None:
        available = list(_registry.keys())
        raise KeyError(
            f"No plugin registered for agent_id={agent_id!r}. "
            f"Available: {available}"
        )
    return plugin


def list_plugins() -> list[str]:
    """Return registered plugin IDs — useful for health checks."""
    return list(_registry.keys())
```

### 1d. `server/app/agent/core/runtime_adapter.py`

**Full file content:**

```python
"""
runtime_adapter.py — Thin async wrapper around graph.invoke.

Keeps the event loop unblocked by running the synchronous LangGraph
invoke on a thread pool via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)


async def invoke_graph(
    plugin: PluginBase,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run plugin.build_graph().invoke(state, config) on a thread.

    Returns the new state dict produced by the graph.
    Propagates any exception raised by the graph (caller must handle).
    """
    graph = plugin.build_graph()
    logger.debug("[RuntimeAdapter] Invoking graph for plugin=%s", plugin.plugin_id)
    result = await asyncio.to_thread(graph.invoke, state, config)
    logger.debug("[RuntimeAdapter] Graph invoke complete for plugin=%s", plugin.plugin_id)
    return result
```

---

## Step 2 — Create the `plugins/` package scaffold

### 2a. `server/app/agent/plugins/__init__.py`

Create empty file.

```python
```

### 2b. `server/app/agent/plugins/mortgage/__init__.py`

Create empty file.

```python
```

---

## Step 3 — Move existing graph and tools files

These are **moves only** — file content is unchanged.

```bash
cd /Users/jamescregeen/A2UI_S2S/server/app/agent

# Create destination directory
mkdir -p plugins/mortgage

# Move files (git mv preserves history)
git mv graph.py plugins/mortgage/graph.py
git mv tools.py plugins/mortgage/tools.py
```

**Verify the move did not break internal imports:**

The import at `plugins/mortgage/graph.py` line 12 is:
```python
from .tools import calculate_ltv, fetch_mortgage_products, recalculate_monthly_payment
```
This relative import is still correct after the move (both files are in the same `plugins/mortgage/` package). No content change needed.

---

## Step 4 — Create `plugins/mortgage/plugin.py`

**Full file content:**

```python
"""
plugin.py — MortgagePlugin: wraps the existing LangGraph mortgage graph.

This is the Phase 1 wrapper. State shape is identical to what main.py
created before refactoring; no mortgage logic changes here.
"""

from __future__ import annotations

from typing import Any, Dict

from app.agent.core.contracts import PluginBase


class MortgagePlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return "mortgage"

    def build_graph(self):
        # Lazy import so the module is not loaded until needed.
        # Caching is handled by Python's module system.
        from app.agent.plugins.mortgage.graph import app_graph
        return app_graph

    def create_initial_state(self) -> Dict[str, Any]:
        """
        Identical to the create_initial_state() that was in main.py.
        Copied here so main.py can delegate to the plugin.
        All mortgage-specific top-level keys are preserved for Phase 1–2 compat.
        """
        return {
            # ── CommonState envelope ──────────────────────────────────────
            "mode": "text",
            "device": "desktop",
            "transcript": "",
            "messages": [],
            "ui": {"surfaceId": "main", "state": "LOADING"},
            "errors": None,
            "pendingAction": None,
            "outbox": [],
            "meta": {},
            "domain": {},          # mortgage keys still at top level in Phase 1
            "state_version": self.state_version,
            # ── Mortgage-specific top-level keys (Phase 1 compat) ─────────
            "intent": {
                "propertyValue": None,
                "loanBalance": None,
                "fixYears": None,
                "termYears": 25,
            },
            "ltv": 0.0,
            "products": [],
            "selection": {},
            "existing_customer": None,
            "property_seen": None,
            "trouble_count": 0,
            "show_support": False,
            "address_validation_failed": False,
            "last_attempted_address": None,
            "branch_requested": False,
            "process_question": None,
        }

    @property
    def capabilities(self) -> Dict[str, Any]:
        return {
            "voice_greeting": "Hello, I'm your Barclays mortgage assistant.",
            "supported_components": [
                "Column", "Row", "Text", "Gauge", "ProductCard",
                "Button", "Map", "Timeline", "DataCard", "BenefitCard",
                "ComparisonBadge", "Image", "StatCard", "ProgressBar",
            ],
        }
```

---

## Step 5 — Modify `server/app/main.py`

These are **surgical edits only**. Make each change independently and verify the server starts after each one.

### Edit 5a — Replace the graph import (line 15)

**Before:**
```python
from .agent.graph import app_graph, AgentState
```

**After:**
```python
from .agent.core.contracts import PluginBase
from .agent.core.registry import register, get_plugin
from .agent.core.runtime_adapter import invoke_graph
from .agent.plugins.mortgage.plugin import MortgagePlugin

# Register all plugins at startup.
# Add new plugins here when created in Phase 2.
register(MortgagePlugin())
```

### Edit 5b — Update WebSocket endpoint signature (line 213)

**Before:**
```python
async def websocket_endpoint(websocket: WebSocket):
```

**After:**
```python
async def websocket_endpoint(websocket: WebSocket, agent: str = "mortgage"):
```

FastAPI reads the `?agent=` query parameter automatically from this signature.

### Edit 5c — Add unknown-agent guard + agent_id to session (lines 215–224)

**Before:**
```python
    await websocket.accept()
    session_id = f"sess_{id(websocket)}"
    logger.info(f"[WebSocket] New connection: {session_id}")

    sessions[session_id] = {
        "state": create_initial_state(),
        "voice_playing": False,
        "tts_task": None,
        "sonic": None,
        "user_transcripts": []
    }
```

**After:**
```python
    # Validate agent_id before accepting so we can reject with a close code.
    try:
        plugin = get_plugin(agent)
    except KeyError as exc:
        await websocket.accept()
        await websocket.close(code=4000, reason=str(exc))
        logger.error("[WebSocket] Unknown agent_id=%r, closing with 4000", agent)
        return

    await websocket.accept()
    session_id = f"sess_{id(websocket)}"
    logger.info("[WebSocket] New connection: %s (agent=%s)", session_id, agent)

    sessions[session_id] = {
        "agent_id": agent,
        "state": plugin.create_initial_state(),
        "voice_playing": False,
        "tts_task": None,
        "sonic": None,
        "user_transcripts": []
    }
```

### Edit 5d — Replace `create_initial_state()` function (lines 38–60)

**Before:**
```python
def create_initial_state() -> AgentState:
    return {
        "mode": "text",
        "device": "desktop",
        "transcript": "",
        "messages": [],
        "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25},
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
    }
```

**After:**
```python
# create_initial_state is now delegated to the plugin.
# Kept as a module-level helper only for any legacy call sites
# not yet updated; Phase 1 removes all such sites.
```

(The function body is no longer needed because the session creation in Edit 5c calls `plugin.create_initial_state()` directly.)

### Edit 5e — Replace four `app_graph.invoke` calls

There are four sites. Replace each with the pattern shown below.

**Pattern to apply at each site:**
```python
# Before (at each of the four sites):
res = await asyncio.to_thread(app_graph.invoke, <state_var>, config)

# After:
plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
res = await invoke_graph(plugin, <state_var>, config)
```

**Site 1 — initial render (approx line 237):**
```python
# Before:
initial_res = await asyncio.to_thread(app_graph.invoke, sessions[session_id]["state"], config)

# After:
initial_res = await invoke_graph(plugin, sessions[session_id]["state"], config)
```
Note: `plugin` is already in scope here from Edit 5c — no need to call `get_plugin` again.

**Site 2 — voice handle_finished (approx line 317):**
```python
# Before:
res = await asyncio.to_thread(app_graph.invoke, current_state, config)

# After:
_plugin = get_plugin(session_data.get("agent_id", "mortgage"))
res = await invoke_graph(_plugin, current_state, config)
```
Note: `handle_finished` is a nested function; use `_plugin` to avoid shadowing the outer `plugin`.

**Site 3 — client.text handler (approx line 416):**
```python
# Before:
res = await asyncio.to_thread(app_graph.invoke, state, config)

# After:
_plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
res = await invoke_graph(_plugin, state, config)
```

**Site 4 — client.ui.action handler (approx line 443):**
```python
# Before:
res = await asyncio.to_thread(app_graph.invoke, current_state, config)

# After:
_plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
res = await invoke_graph(_plugin, current_state, config)
```

### Edit 5f — Replace device-change handler (approx lines 469–484)

This handler currently imports private rendering functions directly from the mortgage graph.
Replace with a full graph invoke so the plugin handles re-rendering.

**Before (the inner `if new_device and new_device != old_device:` block):**
```python
                if new_device and new_device != old_device:
                    from app.agent.graph import render_missing_inputs, render_products_a2ui

                    # Determine which rendering logic to use based on current progress
                    intent = state.get("intent", {})
                    if intent.get("category") and state.get("products"):
                        update = render_products_a2ui(state)
                    else:
                        update = render_missing_inputs(state)

                    # Extract outbox items and send them
                    if "outbox" in update:
                        for item in update["outbox"]:
                            await websocket.send_json(item)
                        # Sync state updates back to session
                        state.update({k: v for k, v in update.items() if k != "outbox"})
```

**After:**
```python
                if new_device and new_device != old_device:
                    # Clear transcript and pendingAction so start_router
                    # does not re-interpret the last user message.
                    state["transcript"] = ""
                    state["pendingAction"] = None
                    lf_callback = get_langfuse_callback()
                    config = {
                        "callbacks": [lf_callback],
                        "metadata": {"langfuse_session_id": sid},
                    }
                    _plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
                    try:
                        res = await invoke_graph(_plugin, state, config)
                        sessions[sid]["state"] = res
                        await process_outbox(websocket, sid)
                    except Exception as e:
                        logger.error("Error re-rendering on device change: %s", e)
```

---

## Step 6 — Verify the migration

Run all verification steps in order. Do not proceed to Phase 2 until all pass.

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# 1. No direct mortgage graph imports remain in main.py
grep -n "from .agent.graph" server/app/main.py
# Expected: no output

# 2. Plugin import chain works
python -c "
from app.agent.core.contracts import PluginBase, CommonState, ServerEvent
from app.agent.core.registry import register, get_plugin
from app.agent.core.runtime_adapter import invoke_graph
from app.agent.plugins.mortgage.plugin import MortgagePlugin
register(MortgagePlugin())
p = get_plugin('mortgage')
s = p.create_initial_state()
assert 'outbox' in s
assert 'domain' in s
print('Plugin import chain: OK')
"

# 3. Mortgage graph still compiles
python -c "
from app.agent.plugins.mortgage.graph import app_graph
print('Mortgage graph: OK')
"

# 4. Unit tests
python -m pytest server/tests/test_math.py -v

# 5. Server starts cleanly
cd server && uvicorn app.main:app --port 8001 &
sleep 4
curl -s http://localhost:8001/docs | grep -q "title" && echo "Server: OK"
kill %1
```

---

## Rollback Plan

If anything breaks before you commit:

```bash
cd /Users/jamescregeen/A2UI_S2S/server/app/agent

# Undo file moves
git mv plugins/mortgage/graph.py graph.py
git mv plugins/mortgage/tools.py tools.py

# Then revert main.py
git checkout server/app/main.py
```

---

## Acceptance Criteria (Definition of Done)

- [ ] `grep -rn "from .agent.graph" server/app/main.py` returns no matches
- [ ] `grep -rn "app_graph" server/app/main.py` returns no matches
- [ ] `grep -rn "AgentState" server/app/main.py` returns no matches
- [ ] `grep -rn "render_missing_inputs\|render_products_a2ui" server/app/main.py` returns no matches
- [ ] `python -m pytest server/tests/test_math.py -v` all pass
- [ ] Server starts and the mortgage flow works end-to-end in manual test
- [ ] `?agent=mortgage` query param accepted (no change to existing connections — default is mortgage)
- [ ] `?agent=unknown` causes WS to close with code 4000

---

## What Has NOT Changed

- `WebSocketMessage` shape in `models.py`
- `process_outbox()` logic
- `run_tts_inline()` logic
- All `server.*` outbox event types
- Client code (`useMortgageSocket.ts`, `A2Renderer.tsx`, `page.tsx`)
- Mortgage graph logic (`plugins/mortgage/graph.py` content unchanged)
- Mortgage tools (`plugins/mortgage/tools.py` content unchanged)

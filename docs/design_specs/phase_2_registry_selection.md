# Phase 2 Design Spec — Agent Registry Selection

**Depends on:** Phase 1 complete and all Phase 1 acceptance criteria met
**Branch suggestion:** `feat/phase-2-registry-selection`
**Estimated files changed:** 5 created, 1 modified

## Objective

Register a second plugin (`lost_card`) alongside the mortgage plugin. The WebSocket runtime
selects the active plugin by reading `?agent=` from the connection URL. The Lost Card plugin
is a working stub — it responds meaningfully and proves the registry works — but contains
no real domain logic (that comes in Phase 4).

---

## Pre-flight Check

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# Phase 1 acceptance check
grep -rn "from .agent.graph" server/app/main.py  # must be empty
python -m pytest server/tests/test_math.py -v    # must all pass
```

---

## Step 1 — Create `plugins/lost_card/` scaffold

### 1a. `server/app/agent/plugins/lost_card/__init__.py`

Empty file.

```python
```

### 1b. `server/app/agent/plugins/lost_card/tools.py`

Stub tool implementations that simulate real banking API calls.
All functions return realistic-looking data; no external calls are made.

```python
"""
tools.py — Lost Card domain tool stubs.

These simulate banking API calls. Each function is idempotent.
Replace with real API calls in production; the interface is stable.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


def freeze_card(card_last4: str) -> Dict[str, Any]:
    """
    Simulate freezing a card.

    Idempotent — safe to call multiple times.
    Returns a result dict with status and timestamp.
    """
    return {
        "success": True,
        "card_last4": card_last4,
        "status": "frozen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Card ending {card_last4} has been frozen immediately.",
    }


def request_replacement(
    card_last4: str,
    delivery_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Simulate ordering a replacement card.

    Idempotent — duplicate requests return the same ETA.
    delivery_address defaults to the address on file if None.
    """
    eta = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%A %-d %B")
    return {
        "success": True,
        "card_last4": card_last4,
        "replacement_requested": True,
        "estimated_arrival": eta,
        "delivery_address": delivery_address or "your address on file",
        "message": f"A new card will arrive by {eta}.",
    }


def get_recent_transactions(card_last4: str) -> List[Dict[str, Any]]:
    """
    Return the last five transactions for a card (stubbed data).

    In production this calls the transactions API.
    """
    return [
        {"date": "24 Feb", "merchant": "Tesco Superstore", "amount": -42.30, "currency": "GBP"},
        {"date": "23 Feb", "merchant": "TfL Travel", "amount": -6.80, "currency": "GBP"},
        {"date": "22 Feb", "merchant": "Costa Coffee", "amount": -4.50, "currency": "GBP"},
        {"date": "22 Feb", "merchant": "Amazon.co.uk", "amount": -29.99, "currency": "GBP"},
        {"date": "21 Feb", "merchant": "Sainsbury's", "amount": -67.14, "currency": "GBP"},
    ]


def unfreeze_card(card_last4: str) -> Dict[str, Any]:
    """Simulate unfreezing a card (e.g. user found it)."""
    return {
        "success": True,
        "card_last4": card_last4,
        "status": "active",
        "unfrozen_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Card ending {card_last4} has been reactivated.",
    }
```

### 1c. `server/app/agent/plugins/lost_card/graph.py`

A minimal but working LangGraph for the stub. It handles the three most common utterances:
"lost card", "found card", and "suspicious transactions". Everything else gets a friendly
holding message. The full graph (Phase 4) will extend this.

```python
"""
graph.py — Lost Card agent graph (Phase 2 stub).

This graph handles three basic intents and returns a2ui patches and voice.
It is intentionally minimal — Phase 4 replaces the placeholder nodes with
full logic. The state shape and outbox contract are production-ready.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph


# ── State ──────────────────────────────────────────────────────────────────────

class LostCardState(TypedDict):
    # CommonState envelope keys (must match contracts.CommonState)
    mode: str
    device: str
    transcript: str
    messages: Annotated[List[Dict[str, Any]], operator.add]
    ui: Dict[str, Any]
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]
    outbox: Annotated[List[Dict[str, Any]], operator.add]
    meta: Dict[str, Any]
    domain: Dict[str, Any]
    state_version: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_patch(components: list, title: str) -> Dict[str, Any]:
    return {
        "type": "server.a2ui.patch",
        "payload": {
            "updateComponents": {
                "components": components,
            },
            "title": title,
        },
    }


def _status_screen(status: str, detail: str, actions: list) -> list:
    """Build a simple card + action buttons component list."""
    components = [
        {"id": "root", "component": "Column", "children": ["status_card", "actions_row"]},
        {
            "id": "status_card",
            "component": "DataCard",
            "text": "Card Status",
            "data": {"status": status, "detail": detail},
        },
        {"id": "actions_row", "component": "Row", "children": [a["id"] for a in actions]},
    ]
    components.extend(actions)
    return components


# ── Nodes ──────────────────────────────────────────────────────────────────────

def ingest_input(state: LostCardState) -> Dict[str, Any]:
    """Pass-through node. Routing happens in start_router."""
    return {}


def handle_lost_card(state: LostCardState) -> Dict[str, Any]:
    """User has reported a lost or stolen card."""
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", "****")

    voice_text = (
        "I can help you right away. I'll freeze your card immediately to keep it secure. "
        "Would you like me to order a replacement as well?"
    )

    components = _status_screen(
        status="Action Required",
        detail="Your card will be frozen immediately. Tap below to confirm.",
        actions=[
            {
                "id": "btn_freeze",
                "component": "Button",
                "text": "Freeze Card Now",
                "data": {"action": "lost_card.freeze_card", "card_last4": card_last4},
            },
            {
                "id": "btn_found",
                "component": "Button",
                "text": "I Found My Card",
                "data": {"action": "lost_card.card_found"},
            },
        ],
    )

    return {
        "outbox": [
            _make_patch(components, "Lost Card"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final", "payload": {"text": voice_text, "role": "assistant"}},
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "pending_freeze",
        }},
    }


def handle_found_card(state: LostCardState) -> Dict[str, Any]:
    """User says they found their card after reporting it lost."""
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", "****")
    current_status = domain.get("card_status", "active")

    if current_status == "frozen":
        voice_text = (
            "Great news! I can reactivate your card right away. "
            "Shall I unfreeze it for you?"
        )
        btn_text = "Unfreeze My Card"
        btn_action = "lost_card.unfreeze_card"
    else:
        voice_text = "No problem! Your card remains active. Is there anything else I can help with?"
        btn_text = "I'm Done"
        btn_action = "lost_card.reset"

    components = _status_screen(
        status="Card Located",
        detail=voice_text,
        actions=[
            {
                "id": "btn_action",
                "component": "Button",
                "text": btn_text,
                "data": {"action": btn_action, "card_last4": card_last4},
            },
        ],
    )

    return {
        "outbox": [
            _make_patch(components, "Card Found"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final", "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


def handle_suspicious_transactions(state: LostCardState) -> Dict[str, Any]:
    """User reports seeing transactions they don't recognise."""
    domain = state.get("domain", {}).get("lost_card", {})

    voice_text = (
        "I'm sorry to hear that. I'll freeze your card right now to prevent further "
        "unauthorised use, and I'll connect you with our fraud team who can review "
        "the transactions and arrange a refund if needed."
    )

    components = [
        {"id": "root", "component": "Column", "children": ["fraud_card", "btn_freeze", "btn_escalate"]},
        {
            "id": "fraud_card",
            "component": "DataCard",
            "text": "Fraud Alert",
            "data": {
                "status": "Under Review",
                "detail": "We'll freeze your card and escalate to our fraud team.",
            },
        },
        {
            "id": "btn_freeze",
            "component": "Button",
            "text": "Freeze Card & Report Fraud",
            "data": {"action": "lost_card.freeze_card", "fraud": True},
        },
        {
            "id": "btn_escalate",
            "component": "Button",
            "text": "Speak to Fraud Team",
            "data": {"action": "lost_card.escalate_fraud"},
        },
    ]

    return {
        "outbox": [
            _make_patch(components, "Suspicious Transactions"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final", "payload": {"text": voice_text, "role": "assistant"}},
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "risk_level": "high",
            "escalation_required": True,
        }},
    }


def handle_default(state: LostCardState) -> Dict[str, Any]:
    """Catch-all for inputs the stub doesn't yet understand."""
    voice_text = (
        "I'm here to help with your card. "
        "You can say 'I've lost my card', 'I see suspicious transactions', "
        "or 'I found my card'."
    )

    components = [
        {"id": "root", "component": "Column", "children": ["welcome_card"]},
        {
            "id": "welcome_card",
            "component": "DataCard",
            "text": "Card Services",
            "data": {
                "detail": (
                    "I can help you freeze a lost card, order a replacement, "
                    "or investigate suspicious transactions."
                ),
            },
        },
    ]

    return {
        "outbox": [
            _make_patch(components, "Card Services"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final", "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


def clear_pending_action(state: LostCardState) -> Dict[str, Any]:
    """Always the last node. Clears pendingAction before END."""
    return {"pendingAction": None}


# ── Routers ────────────────────────────────────────────────────────────────────

_LOST_KEYWORDS = {"lost", "stolen", "missing", "can't find", "cannot find", "lose"}
_FOUND_KEYWORDS = {"found", "found it", "located", "turns out"}
_FRAUD_KEYWORDS = {"suspicious", "fraud", "fraudulent", "don't recognise", "don't recognize",
                   "didn't make", "unauthorised", "unauthorized", "strange transaction"}


def start_router(state: LostCardState) -> str:
    if state.get("pendingAction"):
        return "handle_ui_action"

    transcript = (state.get("transcript") or "").lower()

    if not transcript:
        return "handle_default"

    if any(kw in transcript for kw in _FRAUD_KEYWORDS):
        return "handle_suspicious_transactions"
    if any(kw in transcript for kw in _FOUND_KEYWORDS):
        return "handle_found_card"
    if any(kw in transcript for kw in _LOST_KEYWORDS):
        return "handle_lost_card"

    return "handle_default"


def handle_ui_action(state: LostCardState) -> Dict[str, Any]:
    """
    Phase 2 stub: acknowledge all UI actions with a holding message.
    Phase 4 will replace this with full action dispatch.
    """
    action = state.get("pendingAction", {})
    action_id = (action.get("data") or {}).get("action") or action.get("id", "")

    voice_text = f"Got it — processing {action_id.replace('lost_card.', '').replace('_', ' ')}."
    return {
        "outbox": [
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final", "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


# ── Graph assembly ─────────────────────────────────────────────────────────────

builder = StateGraph(LostCardState)

builder.add_node("ingest_input", ingest_input)
builder.add_node("handle_lost_card", handle_lost_card)
builder.add_node("handle_found_card", handle_found_card)
builder.add_node("handle_suspicious_transactions", handle_suspicious_transactions)
builder.add_node("handle_default", handle_default)
builder.add_node("handle_ui_action", handle_ui_action)
builder.add_node("clear_pending_action", clear_pending_action)

builder.add_edge(START, "ingest_input")
builder.add_conditional_edges(
    "ingest_input",
    start_router,
    {
        "handle_lost_card": "handle_lost_card",
        "handle_found_card": "handle_found_card",
        "handle_suspicious_transactions": "handle_suspicious_transactions",
        "handle_default": "handle_default",
        "handle_ui_action": "handle_ui_action",
    },
)

builder.add_edge("handle_lost_card", "clear_pending_action")
builder.add_edge("handle_found_card", "clear_pending_action")
builder.add_edge("handle_suspicious_transactions", "clear_pending_action")
builder.add_edge("handle_default", "clear_pending_action")
builder.add_edge("handle_ui_action", "clear_pending_action")
builder.add_edge("clear_pending_action", END)

app_graph = builder.compile()
```

### 1d. `server/app/agent/plugins/lost_card/plugin.py`

```python
"""
plugin.py — LostCardPlugin.

Wraps the Lost Card LangGraph agent. State uses the CommonState envelope
with domain data under state["domain"]["lost_card"].
"""

from __future__ import annotations

from typing import Any, Dict

from app.agent.core.contracts import PluginBase


class LostCardPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return "lost_card"

    def build_graph(self):
        from app.agent.plugins.lost_card.graph import app_graph
        return app_graph

    def create_initial_state(self) -> Dict[str, Any]:
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
            "state_version": self.state_version,
            # ── Domain data ───────────────────────────────────────────────
            "domain": {
                "lost_card": {
                    "card_status": "active",    # "active" | "frozen" | "cancelled"
                    "card_last4": None,          # populated when user provides it
                    "freeze_confirmed": False,
                    "replacement_requested": False,
                    "replacement_eta": None,
                    "identity_verified": False,
                    "risk_level": "low",         # "low" | "medium" | "high"
                    "suspicious_tx": [],
                    "branch_requested": False,
                    "escalation_required": False,
                },
            },
        }

    @property
    def capabilities(self) -> Dict[str, Any]:
        return {
            "voice_greeting": "Hello, I can help you with your lost or stolen card.",
            "supported_components": [
                "Column", "Row", "DataCard", "Button", "Timeline",
            ],
        }
```

---

## Step 2 — Register `LostCardPlugin` in `main.py`

Add one import and one register call to the existing plugin registration block created in Phase 1.

**Find this block in `main.py` (at the top, after the Phase 1 changes):**
```python
from .agent.plugins.mortgage.plugin import MortgagePlugin

register(MortgagePlugin())
```

**Replace with:**
```python
from .agent.plugins.mortgage.plugin import MortgagePlugin
from .agent.plugins.lost_card.plugin import LostCardPlugin

register(MortgagePlugin())
register(LostCardPlugin())
```

---

## Step 3 — Verify

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# 1. Both plugins import cleanly
python -c "
from app.agent.core.registry import register, get_plugin
from app.agent.plugins.mortgage.plugin import MortgagePlugin
from app.agent.plugins.lost_card.plugin import LostCardPlugin
register(MortgagePlugin())
register(LostCardPlugin())

m = get_plugin('mortgage')
l = get_plugin('lost_card')
assert m.plugin_id == 'mortgage'
assert l.plugin_id == 'lost_card'
print('Registry: OK')
"

# 2. Lost card graph invokes without error on initial state
python -c "
from app.agent.plugins.lost_card.plugin import LostCardPlugin
p = LostCardPlugin()
s = p.create_initial_state()
g = p.build_graph()
result = g.invoke(s)
assert 'outbox' in result
assert len(result['outbox']) > 0
print('Lost card graph invoke: OK, outbox events:', len(result['outbox']))
"

# 3. Lost card graph handles 'I lost my card'
python -c "
from app.agent.plugins.lost_card.plugin import LostCardPlugin
p = LostCardPlugin()
s = p.create_initial_state()
s['transcript'] = \"I've lost my card\"
g = p.build_graph()
result = g.invoke(s)
voice = [e for e in result['outbox'] if e['type'] == 'server.voice.say']
assert len(voice) == 1, f'Expected 1 voice event, got {len(voice)}'
print('Lost card lost-card path: OK')
print('Voice text:', voice[0]['payload']['text'][:60])
"

# 4. Unit tests still pass
python -m pytest server/tests/test_math.py -v

# 5. Server starts and both agent params work
cd server && uvicorn app.main:app --port 8001 &
sleep 4

# Test WS connection with lost_card agent (uses wscat if installed, else skip)
which wscat && echo '{"type":"client.text","sessionId":"test","payload":{"text":"I lost my card"}}' | \
    wscat -c "ws://localhost:8001/ws?agent=lost_card" --wait 3 || echo "wscat not installed — do manual test"

# Test unknown agent returns 4000
python -c "
import asyncio, websockets

async def test_unknown():
    try:
        async with websockets.connect('ws://localhost:8001/ws?agent=unknown') as ws:
            await ws.recv()
    except websockets.exceptions.ConnectionClosedError as e:
        assert e.code == 4000, f'Expected 4000, got {e.code}'
        print('Unknown agent close code 4000: OK')

asyncio.run(test_unknown())
"

kill %1
```

---

## Acceptance Criteria (Definition of Done)

- [ ] `get_plugin('mortgage')` and `get_plugin('lost_card')` both return valid plugin instances
- [ ] `ws://localhost:8000/ws` connects and delivers mortgage flow (no change from Phase 1)
- [ ] `ws://localhost:8000/ws?agent=mortgage` same as above
- [ ] `ws://localhost:8000/ws?agent=lost_card` connects, receives a UI patch and voice response on first message
- [ ] `ws://localhost:8000/ws?agent=unknown` closes with WebSocket code 4000
- [ ] Lost card graph handles `"I've lost my card"`, `"I found my card"`, `"suspicious transactions"` distinctly
- [ ] Lost card state has all CommonState keys present in `create_initial_state()`
- [ ] `python -m pytest server/tests/test_math.py -v` still passes

---

## What Has NOT Changed

- Mortgage graph logic (unchanged from Phase 1)
- `process_outbox()` function
- `WebSocketMessage` / `models.py`
- Client code

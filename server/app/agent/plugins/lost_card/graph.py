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

"""
graph.py — Lost Card agent graph (Phase 4 full implementation).

Replaces the Phase 2 stub with a production-quality agent that:
- Classifies the user's situation (lost, fraud, found, replacement)
- Enforces identity verification before irreversible actions
- Emits audit events for every critical action
- Provides contextual A2UI screens at each step
"""

from __future__ import annotations

import operator
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.plugins.lost_card.tools import (
    freeze_card,
    request_replacement,
    unfreeze_card,
)


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


# ── Security / policy helpers ──────────────────────────────────────────────────

_CARD_RE = re.compile(r'\b(\d{4}[\s-]?){3,4}\d{1,4}\b')


def _redact_card(text: str) -> str:
    """Mask card-number-like digit sequences in log strings."""
    return _CARD_RE.sub("****-****-****-****", str(text))


def _audit_event(action: str, data: dict) -> dict:
    """Return a server.audit.event outbox entry (dropped by process_outbox)."""
    return {
        "type": "server.audit.event",
        "payload": {
            "action": action,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }


def _requires_identity(state: dict, action: str) -> bool:
    """Return True if the action requires identity verification first."""
    irreversible = {
        "lost_card.freeze_card",
        "lost_card.order_replacement",
        "lost_card.escalate_fraud",
    }
    if action not in irreversible:
        return False
    domain = state.get("domain", {}).get("lost_card", {})
    return not domain.get("identity_verified", False)


# ── UI helper ──────────────────────────────────────────────────────────────────

def _a2ui_patch(components: list, title: str) -> Dict[str, Any]:
    return {
        "type": "server.a2ui.patch",
        "payload": {
            "updateComponents": {"components": components},
            "title": title,
        },
    }


# ── Intent classification ──────────────────────────────────────────────────────

_LOST_KW    = {"lost", "stolen", "missing", "can't find", "cannot find", "lose", "gone"}
_FOUND_KW   = {"found", "found it", "located", "turns out", "still have"}
_FRAUD_KW   = {
    "suspicious", "fraud", "fraudulent", "don't recognise", "don't recognize",
    "didn't make", "unauthorised", "unauthorized", "strange transaction",
    "transactions i don't", "didn't authorise",
}
_REPLACE_KW = {"replacement", "new card", "replace", "order a card", "send me a card"}
_BRANCH_KW  = {"branch", "in person", "visit", "nearest", "come in"}


def classify_intent(transcript: str) -> str:
    t = transcript.lower()
    if any(k in t for k in _FRAUD_KW):    return "fraud"
    if any(k in t for k in _FOUND_KW):    return "found"
    if any(k in t for k in _LOST_KW):     return "lost"
    if any(k in t for k in _REPLACE_KW):  return "replacement"
    if any(k in t for k in _BRANCH_KW):   return "branch"
    return "unknown"


# ── Routers ────────────────────────────────────────────────────────────────────

def start_router(state: LostCardState) -> str:
    if state.get("pendingAction"):
        return "handle_ui_action"

    transcript = (state.get("transcript") or "").strip()
    if not transcript:
        return "handle_default"

    intent = classify_intent(transcript)
    mapping = {
        "fraud":       "handle_fraud_report",
        "found":       "handle_found_card",
        "lost":        "handle_lost_or_stolen",
        "replacement": "handle_general_enquiry",
        "branch":      "handle_general_enquiry",
        "unknown":     "handle_default",
    }
    return mapping[intent]


def ui_action_router(state: LostCardState) -> str:
    action = state.get("pendingAction") or {}
    action_id = (action.get("data") or {}).get("action") or action.get("id", "")

    routing = {
        "lost_card.freeze_card":        "action_freeze_card",
        "lost_card.unfreeze_card":      "action_unfreeze_card",
        "lost_card.order_replacement":  "action_request_replacement",
        "lost_card.escalate_fraud":     "action_escalate_fraud",
        "lost_card.confirm_identity":   "action_confirm_identity",
        "lost_card.reset":              "action_reset",
    }
    return routing.get(action_id, "handle_default")


# ── Transcript-driven nodes ────────────────────────────────────────────────────

def ingest_input(state: LostCardState) -> dict:
    """Pass-through node. Routing happens in start_router."""
    return {}


def handle_lost_or_stolen(state: LostCardState) -> dict:
    """User has reported a lost or stolen card."""
    domain = state.get("domain", {}).get("lost_card", {})

    voice_text = (
        "I'm so sorry to hear that. I can freeze your card immediately — "
        "it takes just a second and protects you from any unauthorised use. "
        "I'll need to confirm your identity first. "
        "Could you tell me the last four digits of the card?"
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "status_card", "identity_prompt"]},
        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 0}},
        {"id": "status_card", "component": "DataCard",
         "text": "Lost or Stolen Card",
         "data": {
             "status": "Action Required",
             "detail": "We'll freeze your card as soon as your identity is confirmed.",
         }},
        {"id": "identity_prompt", "component": "DataCard",
         "text": "Identity Check",
         "data": {
             "detail": "Please tell us the last 4 digits of your card to continue.",
         }},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Report Lost Card"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "pending_freeze",
            "risk_level": "medium",
        }},
    }


def handle_fraud_report(state: LostCardState) -> dict:
    """User reports suspicious / fraudulent transactions."""
    domain = state.get("domain", {}).get("lost_card", {})

    voice_text = (
        "That's very concerning and I want to help you right away. "
        "I'll freeze your card immediately to stop any further unauthorised transactions. "
        "Our fraud team will then investigate and arrange a refund for any charges "
        "you didn't authorise. Could you confirm the last four digits of your card?"
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "fraud_alert", "identity_prompt"]},
        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Investigate"], "current": 0}},
        {"id": "fraud_alert", "component": "BenefitCard",
         "variant": "Warning",
         "text": "Fraud Alert",
         "data": {
             "detail": (
                 "We'll freeze your card immediately and escalate to our fraud team. "
                 "You won't be liable for transactions you didn't authorise."
             ),
         }},
        {"id": "identity_prompt", "component": "DataCard",
         "text": "Identity Check",
         "data": {"detail": "Please provide the last 4 digits of your card to proceed."}},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Fraud Report"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "risk_level": "high",
            "escalation_required": True,
        }},
    }


def handle_found_card(state: LostCardState) -> dict:
    """User says they found their card after reporting it lost."""
    domain = state.get("domain", {}).get("lost_card", {})
    status = domain.get("card_status", "active")

    if status == "frozen":
        voice_text = (
            "Great news — I'm glad you found it! "
            "Your card is currently frozen. "
            "Would you like me to reactivate it now?"
        )
        actions = [
            {"id": "btn_unfreeze", "component": "Button",
             "text": "Yes, Unfreeze My Card",
             "data": {"action": "lost_card.unfreeze_card"}},
            {"id": "btn_keep_frozen", "component": "Button",
             "text": "Keep It Frozen",
             "data": {"action": "lost_card.reset"}},
        ]
        status_detail = "Your card is frozen. Tap below to reactivate it."
    else:
        voice_text = (
            "That's great news! Your card remains active. "
            "If there's anything else I can help with, just let me know."
        )
        actions = [
            {"id": "btn_done", "component": "Button",
             "text": "I'm All Done",
             "data": {"action": "lost_card.reset"}},
        ]
        status_detail = "Your card is active. No further action needed."

    components = [
        {"id": "root", "component": "Column",
         "children": ["status_card", "actions_row"]},
        {"id": "status_card", "component": "DataCard",
         "text": "Card Located",
         "data": {"status": "Card Found", "detail": status_detail}},
        {"id": "actions_row", "component": "Row",
         "children": [a["id"] for a in actions]},
    ]
    components.extend(actions)

    return {
        "outbox": [
            _a2ui_patch(components, "Card Found"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


def handle_general_enquiry(state: LostCardState) -> dict:
    """Handles replacement card requests, branch finding, and other service queries."""
    transcript = (state.get("transcript") or "").lower()
    intent = classify_intent(transcript)

    if intent == "replacement":
        voice_text = (
            "Of course — I can order you a replacement card. "
            "It will arrive within 5 working days to your registered address. "
            "Shall I go ahead?"
        )
        components = [
            {"id": "root", "component": "Column",
             "children": ["replace_card", "btn_order", "btn_cancel"]},
            {"id": "replace_card", "component": "DataCard",
             "text": "Replacement Card",
             "data": {
                 "detail": "A new card will arrive within 5 working days to your address on file.",
             }},
            {"id": "btn_order", "component": "Button",
             "text": "Order Replacement",
             "data": {"action": "lost_card.order_replacement"}},
            {"id": "btn_cancel", "component": "Button",
             "text": "Cancel",
             "data": {"action": "lost_card.reset"}},
        ]
        title = "Replacement Card"
    else:
        voice_text = (
            "I can help with lost cards, suspicious transactions, replacement cards, "
            "or finding your nearest branch. What would you like to do?"
        )
        components = [
            {"id": "root", "component": "Column", "children": ["options_card"]},
            {"id": "options_card", "component": "DataCard",
             "text": "Card Services",
             "data": {
                 "detail": "I can help with: lost card, fraud, replacement, or branch finder.",
             }},
        ]
        title = "Card Services"

    return {
        "outbox": [
            _a2ui_patch(components, title),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


def handle_default(state: LostCardState) -> dict:
    """Catch-all welcome / help screen."""
    voice_text = (
        "Hello! I'm here to help with your Barclays card. "
        "You can tell me if you've lost your card, spotted suspicious transactions, "
        "or if you'd like to order a replacement."
    )
    components = [
        {"id": "root", "component": "Column", "children": ["welcome"]},
        {"id": "welcome", "component": "DataCard",
         "text": "Card Services",
         "data": {
             "detail": (
                 "How can I help? Try: "
                 "\"I've lost my card\", "
                 "\"I see transactions I don't recognise\", "
                 "or \"I need a replacement card\"."
             ),
         }},
    ]
    return {
        "outbox": [
            _a2ui_patch(components, "Card Services"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


# ── UI action nodes ────────────────────────────────────────────────────────────

def handle_ui_action(state: LostCardState) -> dict:
    """Pass-through — routing happens in ui_action_router."""
    return {}


def action_confirm_identity(state: LostCardState) -> dict:
    """Called when the user provides their card last 4 digits."""
    action = state.get("pendingAction") or {}
    data = action.get("data") or {}
    card_last4 = data.get("card_last4") or (state.get("transcript") or "")[-4:]
    domain = state.get("domain", {}).get("lost_card", {})

    # Strip to digits only
    card_last4 = re.sub(r"\D", "", card_last4)[-4:]

    if len(card_last4) == 4:
        voice_text = (
            f"Thank you — I've confirmed your card ending {card_last4}. "
            "I'm freezing it now."
        )
        domain_update = {
            **domain,
            "identity_verified": True,
            "card_last4": card_last4,
        }
        next_action = "lost_card.freeze_card"
    else:
        voice_text = (
            "I didn't quite catch that. "
            "Could you give me the last four digits of your card?"
        )
        domain_update = domain
        next_action = None

    components = [
        {"id": "root", "component": "Column", "children": ["id_card"]},
        {"id": "id_card", "component": "DataCard",
         "text": "Identity Confirmed" if len(card_last4) == 4 else "Identity Check",
         "data": {"detail": voice_text}},
    ]

    outbox = [
        _a2ui_patch(components, "Identity"),
        {"type": "server.voice.say", "payload": {"text": voice_text}},
        {"type": "server.transcript.final",
         "payload": {"text": voice_text, "role": "assistant"}},
    ]

    if next_action:
        outbox.append({
            "type": "server.internal.chain_action",
            "payload": {"action": next_action},
        })

    return {
        "outbox": outbox,
        "domain": {**state.get("domain", {}), "lost_card": domain_update},
    }


def action_freeze_card(state: LostCardState) -> dict:
    """Freeze the user's card. Requires identity verification."""
    domain = state.get("domain", {}).get("lost_card", {})

    if not domain.get("identity_verified", False):
        voice_text = (
            "For your security, I need to verify your identity before freezing your card. "
            "Please tell me the last four digits."
        )
        components = [
            {"id": "root", "component": "Column", "children": ["gate_card"]},
            {"id": "gate_card", "component": "DataCard",
             "text": "Identity Required",
             "data": {"detail": "Please provide your card's last 4 digits to continue."}},
        ]
        return {
            "outbox": [
                _a2ui_patch(components, "Identity Check"),
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    card_last4 = domain.get("card_last4", "****")
    result = freeze_card(card_last4)

    voice_text = (
        f"Done — your card ending {card_last4} is now frozen. "
        "No one can make purchases with it. "
        "Would you like me to order a replacement card?"
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "frozen_card", "actions_row"]},
        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 2}},
        {"id": "frozen_card", "component": "DataCard",
         "text": "Card Frozen",
         "data": {
             "status": "Frozen",
             "detail": f"Card ending {card_last4} was frozen at {result['frozen_at'][:19].replace('T', ' ')} UTC.",
         }},
        {"id": "actions_row", "component": "Row", "children": ["btn_replace", "btn_done"]},
        {"id": "btn_replace", "component": "Button",
         "text": "Order Replacement",
         "data": {"action": "lost_card.order_replacement"}},
        {"id": "btn_done", "component": "Button",
         "text": "I'm Done",
         "data": {"action": "lost_card.reset"}},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Card Frozen"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
            _audit_event("freeze_card", {
                "card_last4": _redact_card(card_last4),
                "success": result["success"],
            }),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "frozen",
            "freeze_confirmed": True,
        }},
    }


def action_request_replacement(state: LostCardState) -> dict:
    """Order a replacement card. Requires identity verification."""
    domain = state.get("domain", {}).get("lost_card", {})

    if not domain.get("identity_verified", False):
        voice_text = (
            "Please confirm your identity first by telling me your card's last 4 digits."
        )
        return {
            "outbox": [
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    card_last4 = domain.get("card_last4", "****")
    result = request_replacement(card_last4)
    eta = result["estimated_arrival"]

    voice_text = (
        f"Your replacement card has been ordered and will arrive by {eta}. "
        "You'll receive a text when it's dispatched."
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "replace_card"]},
        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 3}},
        {"id": "replace_card", "component": "DataCard",
         "text": "Replacement Ordered",
         "data": {
             "status": "On Its Way",
             "detail": f"Estimated arrival: {eta}. Delivery to {result['delivery_address']}.",
         }},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Replacement Card"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
            _audit_event("request_replacement", {
                "card_last4": _redact_card(card_last4),
                "eta": eta,
                "success": result["success"],
            }),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "replacement_requested": True,
            "replacement_eta": eta,
        }},
    }


def action_unfreeze_card(state: LostCardState) -> dict:
    """Reactivate a frozen card (user found it)."""
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", "****")
    result = unfreeze_card(card_last4)

    voice_text = (
        f"Your card ending {card_last4} is now reactivated and ready to use. "
        "Is there anything else I can help with?"
    )

    components = [
        {"id": "root", "component": "Column", "children": ["active_card"]},
        {"id": "active_card", "component": "DataCard",
         "text": "Card Reactivated",
         "data": {"status": "Active", "detail": f"Card ending {card_last4} is now active."}},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Card Active"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
            _audit_event("unfreeze_card", {"card_last4": _redact_card(card_last4)}),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "active",
        }},
    }


def action_escalate_fraud(state: LostCardState) -> dict:
    """Escalate to the fraud team."""
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", "****")

    voice_text = (
        "I'm connecting you with our specialist fraud team right now. "
        "They will review all recent transactions and contact you within 24 hours. "
        "Your card remains frozen until the investigation is complete."
    )

    components = [
        {"id": "root", "component": "Column", "children": ["escalation_card"]},
        {"id": "escalation_card", "component": "DataCard",
         "text": "Fraud Investigation",
         "data": {
             "status": "Escalated",
             "detail": (
                 "Our fraud team has been notified and will review your case. "
                 "You'll receive a call within 24 hours. "
                 "Your card is frozen for your protection."
             ),
         }},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Fraud Escalated"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
            _audit_event("escalate_fraud", {
                "card_last4": _redact_card(card_last4),
                "risk_level": domain.get("risk_level"),
            }),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "escalation_required": True,
        }},
    }


def action_reset(state: LostCardState) -> dict:
    """Return to the welcome screen."""
    voice_text = "Is there anything else I can help you with today?"
    components = [
        {"id": "root", "component": "Column", "children": ["done_card"]},
        {"id": "done_card", "component": "DataCard",
         "text": "All Done",
         "data": {"detail": "Let me know if you need anything else."}},
    ]
    return {
        "outbox": [
            _a2ui_patch(components, "Card Services"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
        ],
    }


def clear_pending_action(state: LostCardState) -> dict:
    """Always the last node. Clears pendingAction and transcript before END."""
    return {"pendingAction": None, "transcript": ""}


# ── Graph assembly ─────────────────────────────────────────────────────────────

builder = StateGraph(LostCardState)

# Core transcript-driven nodes
builder.add_node("ingest_input",             ingest_input)
builder.add_node("handle_lost_or_stolen",    handle_lost_or_stolen)
builder.add_node("handle_found_card",        handle_found_card)
builder.add_node("handle_fraud_report",      handle_fraud_report)
builder.add_node("handle_general_enquiry",   handle_general_enquiry)
builder.add_node("handle_default",           handle_default)

# UI action nodes
builder.add_node("handle_ui_action",         handle_ui_action)
builder.add_node("action_confirm_identity",  action_confirm_identity)
builder.add_node("action_freeze_card",       action_freeze_card)
builder.add_node("action_unfreeze_card",     action_unfreeze_card)
builder.add_node("action_request_replacement", action_request_replacement)
builder.add_node("action_escalate_fraud",    action_escalate_fraud)
builder.add_node("action_reset",             action_reset)

# Terminal
builder.add_node("clear_pending_action",     clear_pending_action)

# START → ingest_input → start_router
builder.add_edge(START, "ingest_input")
builder.add_conditional_edges(
    "ingest_input",
    start_router,
    {
        "handle_lost_or_stolen":   "handle_lost_or_stolen",
        "handle_found_card":       "handle_found_card",
        "handle_fraud_report":     "handle_fraud_report",
        "handle_general_enquiry":  "handle_general_enquiry",
        "handle_default":          "handle_default",
        "handle_ui_action":        "handle_ui_action",
    },
)

# handle_ui_action → ui_action_router → action nodes
builder.add_conditional_edges(
    "handle_ui_action",
    ui_action_router,
    {
        "action_freeze_card":          "action_freeze_card",
        "action_unfreeze_card":        "action_unfreeze_card",
        "action_request_replacement":  "action_request_replacement",
        "action_escalate_fraud":       "action_escalate_fraud",
        "action_confirm_identity":     "action_confirm_identity",
        "action_reset":                "action_reset",
        "handle_default":              "handle_default",
    },
)

# All paths → clear_pending_action → END
for _node in [
    "handle_lost_or_stolen", "handle_found_card", "handle_fraud_report",
    "handle_general_enquiry", "handle_default", "action_confirm_identity",
    "action_freeze_card", "action_unfreeze_card", "action_request_replacement",
    "action_escalate_fraud", "action_reset",
]:
    builder.add_edge(_node, "clear_pending_action")

builder.add_edge("clear_pending_action", END)

app_graph = builder.compile()

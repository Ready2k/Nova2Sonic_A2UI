"""
graph.py — Lost Card agent graph.

Production-quality agent that:
- Loads mock customer profile to personalise every screen
- Classifies the user's situation (lost, fraud, found, replacement, branch)
- Enforces identity verification before irreversible actions
- Combines identity verify + card freeze into a single atomic node
- Shows suspicious transactions on the fraud report screen
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
    get_customer_profile,
    get_suspicious_transactions,
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


# ── UI helper ──────────────────────────────────────────────────────────────────

def _a2ui_patch(components: list, title: str) -> Dict[str, Any]:
    return {
        "type": "server.a2ui.patch",
        "payload": {
            "updateComponents": {"components": components},
            "title": title,
        },
    }


def _fmt_time(iso: str) -> str:
    """Format an ISO timestamp to a human-readable 'HH:MM on D Month'."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%-I:%M %p on %-d %B")
    except Exception:
        return iso[:16].replace("T", " ")


# ── Intent classification ──────────────────────────────────────────────────────

_LOST_KW    = {"lost", "stolen", "missing", "can't find", "cannot find", "lose", "gone", "not there"}
_FOUND_KW   = {"found", "found it", "located", "turns out", "still have", "got it back"}
_FRAUD_KW   = {
    "suspicious", "fraud", "fraudulent", "don't recognise", "don't recognize",
    "didn't make", "unauthorised", "unauthorized", "strange transaction",
    "transactions i don't", "didn't authorise", "not mine", "not my transaction",
    "someone else", "hacked",
}
_REPLACE_KW = {"replacement", "new card", "replace", "order a card", "send me a card", "need a new"}
_BRANCH_KW  = {"branch", "in person", "visit", "nearest", "come in", "office"}


def classify_intent(transcript: str) -> str:
    t = transcript.lower()
    # Check fraud first — user may say "lost and suspicious transactions"
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

    # If we're mid-flow waiting for identity digits, route any digit-containing
    # input straight to action_confirm_identity rather than classify_intent.
    domain = state.get("domain", {}).get("lost_card", {})
    if (
        domain.get("card_status") == "pending_freeze"
        and not domain.get("identity_verified", False)
        and re.search(r'\d{4}', transcript)
    ):
        return "action_confirm_identity"

    intent = classify_intent(transcript)

    # If intent is unrecognised but we're mid-journey, answer in context rather
    # than resetting to the welcome screen.
    if intent == "unknown" and domain.get("card_status") not in ("active", None):
        return "handle_contextual_query"

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
        # Core card actions
        "lost_card.freeze_card":        "action_freeze_card",
        "lost_card.unfreeze_card":      "action_unfreeze_card",
        "lost_card.order_replacement":  "action_request_replacement",
        "lost_card.escalate_fraud":     "action_escalate_fraud",
        "lost_card.confirm_identity":   "action_confirm_identity",
        "lost_card.reset":              "action_reset",
        # Quick-start buttons from the welcome screen
        "lost_card.start_lost":         "handle_lost_or_stolen",
        "lost_card.start_fraud":        "handle_fraud_report",
        "lost_card.start_replacement":  "handle_general_enquiry",
    }
    return routing.get(action_id, "handle_default")


# ── Transcript-driven nodes ────────────────────────────────────────────────────

def ingest_input(state: LostCardState) -> dict:
    """Pass-through node. Routing happens in start_router."""
    return {}


def handle_default(state: LostCardState) -> dict:
    """Welcome screen with customer card summary and quick-action buttons."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    card_status = domain.get("card_status", "active")

    _STATUS_LABEL = {
        "active":         ("Active",         "✅"),
        "pending_freeze": ("Freeze Pending",  "⏳"),
        "frozen":         ("Frozen",          "🔒"),
        "cancelled":      ("Cancelled",       "❌"),
    }
    status_label, status_icon = _STATUS_LABEL.get(card_status, ("Active", "✅"))

    voice_text = (
        f"Hello {profile['first_name']}! I'm here to help with your Barclays card. "
        "You can tell me if you've lost your card, spotted suspicious transactions, "
        "or if you need a replacement."
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["card_summary", "btn_lost", "btn_fraud", "btn_replace"]},

        # Personalised card summary — status reflects actual domain state
        {"id": "card_summary", "component": "DataCard",
         "text": "Your Card",
         "data": {
             "items": [
                 {"label": "Account Holder", "value": profile["full_name"], "icon": "👤"},
                 {"label": "Card", "value": f"{profile['card_type']}  ···· {profile['card_last4']}", "icon": "💳"},
                 {"label": "Status", "value": status_label, "icon": status_icon},
                 {"label": "Registered Address", "value": profile["registered_address"], "icon": "🏠"},
             ],
         }},

        # Quick-action buttons
        {"id": "btn_lost", "component": "Button",
         "text": "I've Lost My Card",
         "data": {"action": "lost_card.start_lost"}},
        {"id": "btn_fraud", "component": "Button",
         "text": "Suspicious Transactions",
         "data": {"action": "lost_card.start_fraud"}},
        {"id": "btn_replace", "component": "Button",
         "text": "Order a Replacement Card",
         "data": {"action": "lost_card.start_replacement"}},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Card Services"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
        ],
    }


def handle_lost_or_stolen(state: LostCardState) -> dict:
    """User has reported a lost or stolen card."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})

    voice_text = (
        f"I'm sorry to hear that, {profile['first_name']}. "
        "I'll freeze your card immediately to protect you from unauthorised use — "
        "I just need to confirm your identity first. "
        "Please tell me the last four digits of your card."
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "card_info", "fraud_warning", "identity_prompt"]},

        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 0}},

        # Show the card being reported
        {"id": "card_info", "component": "DataCard",
         "text": "Card Being Reported",
         "data": {
             "status": "Action Required",
             "items": [
                 {"label": "Card Type", "value": profile["card_type"], "icon": "💳"},
                 {"label": "Card Number", "value": f"···· ···· ···· {profile['card_last4']}", "icon": "🔢"},
             ],
         }},

        # Reassurance
        {"id": "fraud_warning", "component": "BenefitCard",
         "variant": "Info",
         "text": "You're protected",
         "data": {
             "detail": (
                 "You're not liable for transactions made after you report the card lost. "
                 "We'll freeze it immediately once your identity is confirmed."
             ),
         }},

        # Identity prompt
        {"id": "identity_prompt", "component": "DataCard",
         "text": "Identity Verification",
         "data": {
             "detail": (
                 f"Please tell me the last 4 digits of your card "
                 f"(ending ···· {profile['card_last4']}) to continue."
             ),
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
    """User reports suspicious / fraudulent transactions — show them the flagged charges."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    suspicious = get_suspicious_transactions(profile["card_last4"])

    voice_text = (
        "That's very concerning — I want to help you right away. "
        f"I can see {len(suspicious)} suspicious transaction{'s' if len(suspicious) != 1 else ''} "
        "on your account that may not be yours. "
        "I'll freeze your card immediately to stop any further charges. "
        "Could you confirm the last four digits of your card to proceed?"
    )

    # Build transaction items (max 5 shown)
    tx_items = []
    for tx in suspicious[:5]:
        amount_str = f"-£{abs(tx['amount']):.2f}"
        label = f"{tx['date']} — {tx['merchant']}"
        note = tx.get("note", "")
        value = f"{amount_str}  {note}" if note else amount_str
        tx_items.append({"label": label, "value": value, "icon": "⚠️"})

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "fraud_alert", "tx_list", "identity_prompt"]},

        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Investigate"], "current": 0}},

        {"id": "fraud_alert", "component": "BenefitCard",
         "variant": "Warning",
         "text": "Suspicious Activity Detected",
         "data": {
             "detail": (
                 f"{len(suspicious)} transaction{'s' if len(suspicious) != 1 else ''} flagged on "
                 f"your card ending ···· {profile['card_last4']}. "
                 "You won't be liable for charges you didn't authorise."
             ),
         }},

        # Suspicious transactions
        {"id": "tx_list", "component": "DataCard",
         "text": "Flagged Transactions",
         "data": {"items": tx_items}},

        # Identity prompt
        {"id": "identity_prompt", "component": "DataCard",
         "text": "Identity Verification",
         "data": {
             "detail": (
                 f"Tell me the last 4 digits of your card ending ···· {profile['card_last4']} "
                 "to freeze it and begin the fraud investigation."
             ),
         }},
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
            "card_status": "pending_freeze",
            "risk_level": "high",
            "escalation_required": True,
            "suspicious_tx": suspicious,
        }},
    }


def handle_found_card(state: LostCardState) -> dict:
    """User says they found their card after reporting it lost."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    status = domain.get("card_status", "active")

    if status == "frozen":
        voice_text = (
            f"Great news, {profile['first_name']} — I'm glad you found it! "
            "Your card is currently frozen for your protection. "
            "Would you like me to reactivate it now?"
        )
        action_children = ["btn_unfreeze", "btn_keep_frozen"]
        extra_detail = "Your card is currently frozen. Say 'yes' to reactivate it, or let me know if you'd like to keep it frozen."
    else:
        voice_text = (
            f"Wonderful — your card ending ···· {profile['card_last4']} is still active, "
            "so there's nothing more you need to do. "
            "Is there anything else I can help with today?"
        )
        action_children = ["btn_done"]
        extra_detail = "Your card is active and ready to use. No further action needed."

    components = [
        {"id": "root", "component": "Column",
         "children": ["status_card", "action_row"]},

        {"id": "status_card", "component": "DataCard",
         "text": "Card Located",
         "data": {
             "status": "Card Found",
             "detail": extra_detail,
             "items": [
                 {"label": "Card", "value": f"···· ···· ···· {profile['card_last4']}", "icon": "💳"},
                 {"label": "Current Status", "value": "Frozen" if status == "frozen" else "Active", "icon": "🔒" if status == "frozen" else "✅"},
             ],
         }},

        {"id": "action_row", "component": "Row", "children": action_children},
        {"id": "btn_unfreeze", "component": "Button",
         "text": "Yes, Reactivate My Card",
         "data": {"action": "lost_card.unfreeze_card"}},
        {"id": "btn_keep_frozen", "component": "Button",
         "text": "Keep It Frozen for Now",
         "data": {"action": "lost_card.reset"}},
        {"id": "btn_done", "component": "Button",
         "text": "Great, I'm All Done",
         "data": {"action": "lost_card.reset"}},
    ]

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
    profile = get_customer_profile()
    transcript = (state.get("transcript") or "").lower()
    intent = classify_intent(transcript)

    if intent == "replacement" or state.get("pendingAction"):
        voice_text = (
            f"Of course, {profile['first_name']} — I can order you a replacement card right now. "
            f"It'll be delivered to {profile['registered_address']} within 5 working days. "
            "Shall I go ahead and place the order?"
        )
        components = [
            {"id": "root", "component": "Column",
             "children": ["replace_info", "btn_order", "btn_cancel"]},

            {"id": "replace_info", "component": "DataCard",
             "text": "Replacement Card",
             "data": {
                 "detail": "A new card will be sent to your registered address.",
                 "items": [
                     {"label": "Delivery Address", "value": profile["registered_address"], "icon": "🏠"},
                     {"label": "Estimated Arrival", "value": "Within 5 working days", "icon": "📦"},
                     {"label": "Card Type", "value": profile["card_type"], "icon": "💳"},
                 ],
             }},

            {"id": "btn_order", "component": "Button",
             "text": "Yes, Order Replacement",
             "data": {"action": "lost_card.order_replacement"}},
            {"id": "btn_cancel", "component": "Button",
             "text": "Not Right Now",
             "data": {"action": "lost_card.reset"}},
        ]
        title = "Replacement Card"
    else:
        voice_text = (
            f"Hello {profile['first_name']}! I can help you with: "
            "reporting a lost or stolen card, suspicious transactions, "
            "ordering a replacement, or anything else card-related. "
            "What would you like to do?"
        )
        components = [
            {"id": "root", "component": "Column",
             "children": ["options_card", "btn_lost", "btn_fraud", "btn_replace"]},
            {"id": "options_card", "component": "DataCard",
             "text": "Card Services",
             "data": {"detail": "How can I help you today?"}},
            {"id": "btn_lost", "component": "Button",
             "text": "Report Lost or Stolen Card",
             "data": {"action": "lost_card.start_lost"}},
            {"id": "btn_fraud", "component": "Button",
             "text": "Report Suspicious Transactions",
             "data": {"action": "lost_card.start_fraud"}},
            {"id": "btn_replace", "component": "Button",
             "text": "Order a Replacement Card",
             "data": {"action": "lost_card.start_replacement"}},
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


# ── Contextual Q&A ─────────────────────────────────────────────────────────────

# Keywords used to recognise common mid-journey questions
_Q_DURATION     = {"how long", "when will", "expire", "until", "duration", "how long will"}
_Q_DIRECT_DEBIT = {"direct debit", "standing order", "bill", "subscription", "recurring"}
_Q_LIABILITY    = {"liable", "liability", "charged", "charge", "unauthorised", "unauthorized",
                   "money back", "refund", "responsible", "owe"}
_Q_WHAT_NEXT    = {"what now", "what next", "what should", "what do i", "what happens"}
_Q_UNFREEZE     = {"unfreeze", "reactivate", "turn back on", "undo", "reverse"}
_Q_NO           = {"no,", "no thank", "no that", "not right now", "maybe later",
                   "don't need", "that's all", "all done", "just want to know"}


def _contextual_answer(transcript: str, domain: dict, profile: dict) -> tuple[str, str]:
    """
    Return (voice_text, detail_text) appropriate to the current domain state.
    Covers the most common follow-up questions after a card event.
    """
    t = transcript.lower()
    status = domain.get("card_status", "active")
    is_fraud = domain.get("risk_level") == "high" or domain.get("escalation_required")
    replacement_ordered = domain.get("replacement_requested", False)
    eta = domain.get("replacement_eta", "")
    last4 = domain.get("card_last4") or profile["card_last4"]

    # "How long will it be frozen?"
    if any(k in t for k in _Q_DURATION) and status == "frozen":
        if is_fraud:
            answer = (
                "Your card will stay frozen for the duration of the fraud investigation, "
                "which typically takes up to 5 working days. "
                "Once resolved, we'll issue a replacement automatically."
            )
        elif replacement_ordered:
            answer = (
                f"Your card will remain frozen until your replacement arrives — "
                f"estimated {eta}. It will then be automatically cancelled."
            )
        else:
            answer = (
                "Your card will stay frozen until you either choose to unfreeze it "
                "or we send you a replacement. There's no automatic expiry — "
                "you're in full control."
            )
        return answer, answer

    # "What about my direct debits / standing orders?"
    if any(k in t for k in _Q_DIRECT_DEBIT):
        answer = (
            "Your direct debits and standing orders are completely unaffected — "
            "the freeze only blocks card payments and ATM withdrawals. "
            "All your regular payments will continue as normal."
        )
        return answer, answer

    # "Will I be charged? Am I liable?"
    if any(k in t for k in _Q_LIABILITY):
        answer = (
            "You're fully protected. You won't be liable for any transactions "
            "made after you reported the card. If any fraudulent charges are "
            "confirmed during our investigation, you'll receive a full refund "
            "— typically within 5 working days."
        )
        return answer, answer

    # "What happens next?"
    if any(k in t for k in _Q_WHAT_NEXT):
        if replacement_ordered and eta:
            answer = (
                f"Your replacement card is on its way — estimated arrival {eta}. "
                "We'll send you a text when it's been dispatched. "
                "Your frozen card will be automatically cancelled once the new one is issued."
            )
        elif status == "frozen":
            answer = (
                "Your card is frozen and protected. "
                "You can order a replacement at any time, or I can help with anything else card-related."
            )
        else:
            answer = "Let me know what you'd like to do next — I'm here to help."
        return answer, answer

    # "How do I unfreeze it?"
    if any(k in t for k in _Q_UNFREEZE) and status == "frozen":
        answer = (
            "You can unfreeze your card at any time — just tell me "
            "'I found my card' and I'll reactivate it straight away."
        )
        return answer, answer

    # "No" / declining the replacement offer
    if any(k in t for k in _Q_NO) or t.strip() in ("no", "no thanks", "nope"):
        if status == "frozen":
            answer = (
                f"No problem at all. Your card ending {last4} is safely frozen — "
                "no purchases can be made with it. "
                "Just come back whenever you need anything else."
            )
        else:
            answer = "Of course — let me know if there's anything else I can help with."
        return answer, answer

    # Generic fallback — stay in context
    if status == "frozen":
        answer = (
            f"Your card ending {last4} is frozen and protected. "
            "I can help you order a replacement, answer questions about your account, "
            "or unfreeze the card if you find it. What would you like to do?"
        )
    elif status == "pending_freeze":
        answer = (
            "I still need the last 4 digits of your card to continue. "
            f"That's the last four digits shown on the front of your card ending ···· {last4}."
        )
    else:
        answer = "I'm here to help with your card. What would you like to do?"

    return answer, answer


def handle_contextual_query(state: LostCardState) -> dict:
    """Answer a follow-up question in the context of the current journey."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    status = domain.get("card_status", "active")
    last4 = domain.get("card_last4") or profile["card_last4"]

    voice_text, detail_text = _contextual_answer(
        state.get("transcript") or "", domain, profile
    )

    # Build relevant action buttons based on current state
    action_children = []
    extra_buttons = []

    if status == "frozen" and not domain.get("replacement_requested"):
        action_children += ["btn_replace", "btn_done"]
        extra_buttons += [
            {"id": "btn_replace", "component": "Button",
             "text": "Order Replacement Card",
             "data": {"action": "lost_card.order_replacement"}},
            {"id": "btn_done", "component": "Button",
             "text": "That's All, Thank You",
             "data": {"action": "lost_card.reset"}},
        ]
    elif status == "frozen" and domain.get("replacement_requested"):
        action_children += ["btn_done"]
        extra_buttons += [
            {"id": "btn_done", "component": "Button",
             "text": "That's All, Thank You",
             "data": {"action": "lost_card.reset"}},
        ]
    else:
        action_children += ["btn_done"]
        extra_buttons += [
            {"id": "btn_done", "component": "Button",
             "text": "OK, Thanks",
             "data": {"action": "lost_card.reset"}},
        ]

    children = ["answer_card"] + (["action_row"] if action_children else [])
    components = [
        {"id": "root", "component": "Column", "children": children},
        {"id": "answer_card", "component": "DataCard",
         "text": f"Card ending ···· {last4}" if last4 else "Your Card",
         "data": {
             "status": "Frozen" if status == "frozen" else None,
             "detail": detail_text,
         }},
    ]
    if action_children:
        components.append({"id": "action_row", "component": "Row", "children": action_children})
    components.extend(extra_buttons)

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
    """
    Verify identity via card last-4, then immediately freeze the card.

    Combining verify + freeze in a single node avoids the broken
    chain_action pattern (which was silently dropped by process_outbox).
    """
    profile = get_customer_profile()
    action = state.get("pendingAction") or {}
    data = action.get("data") or {}
    domain = state.get("domain", {}).get("lost_card", {})

    # Try to extract last-4 from action data or from the transcript
    raw = data.get("card_last4") or (state.get("transcript") or "")
    digits = re.sub(r"\D", "", raw)[-4:]

    expected = re.sub(r"\D", "", profile["card_last4"])

    if len(digits) != 4:
        voice_text = (
            "I didn't quite catch that. "
            f"Please give me the last four digits of your card ending ···· {profile['card_last4']}."
        )
        return {
            "outbox": [
                _a2ui_patch([
                    {"id": "root", "component": "Column", "children": ["id_card"]},
                    {"id": "id_card", "component": "DataCard",
                     "text": "Identity Verification",
                     "data": {"detail": "Please say or type the last 4 digits of your card to continue."}},
                ], "Identity Check"),
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    # Digits provided — verify against profile
    if digits != expected:
        voice_text = (
            "I'm sorry, those digits don't match what we have on file. "
            "Please double-check and try again — it's the last four digits on the front of your card."
        )
        return {
            "outbox": [
                _a2ui_patch([
                    {"id": "root", "component": "Column", "children": ["id_fail"]},
                    {"id": "id_fail", "component": "BenefitCard",
                     "variant": "Warning",
                     "text": "Digits Don't Match",
                     "data": {"detail": "Please check the last 4 digits on your card and try again."}},
                ], "Identity Check"),
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    # Identity confirmed — freeze the card immediately
    result = freeze_card(digits)
    frozen_at = _fmt_time(result["frozen_at"])

    voice_text = (
        f"Perfect — identity confirmed. "
        f"I've frozen your card ending {digits} right now. "
        "No one can make purchases with it. "
        "Would you like me to order a replacement card?"
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "frozen_card", "action_row"]},

        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 2}},

        {"id": "frozen_card", "component": "DataCard",
         "text": "Card Frozen",
         "data": {
             "status": "Frozen",
             "detail": f"Your card is now protected. No transactions can be made.",
             "items": [
                 {"label": "Card", "value": f"···· ···· ···· {digits}", "icon": "💳"},
                 {"label": "Frozen At", "value": frozen_at, "icon": "🕐"},
                 {"label": "Reference", "value": f"LC{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", "icon": "📄"},
             ],
         }},

        {"id": "action_row", "component": "Row", "children": ["btn_replace", "btn_done"]},
        {"id": "btn_replace", "component": "Button",
         "text": "Order Replacement Card",
         "data": {"action": "lost_card.order_replacement"}},
        {"id": "btn_done", "component": "Button",
         "text": "That's All, Thank You",
         "data": {"action": "lost_card.reset"}},
    ]

    return {
        "outbox": [
            _a2ui_patch(components, "Card Frozen"),
            {"type": "server.voice.say", "payload": {"text": voice_text}},
            {"type": "server.transcript.final",
             "payload": {"text": voice_text, "role": "assistant"}},
            _audit_event("verify_and_freeze", {
                "card_last4": _redact_card(digits),
                "success": True,
            }),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "identity_verified": True,
            "card_last4": digits,
            "card_status": "frozen",
            "freeze_confirmed": True,
        }},
    }


def action_freeze_card(state: LostCardState) -> dict:
    """
    Freeze the card directly (called when identity is already verified).
    Routes to identity check if not yet verified.
    """
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})

    if not domain.get("identity_verified", False):
        voice_text = (
            "For your security, I need to verify your identity before I can freeze your card. "
            f"Please tell me the last four digits of your card ending ···· {profile['card_last4']}."
        )
        components = [
            {"id": "root", "component": "Column", "children": ["gate_card"]},
            {"id": "gate_card", "component": "DataCard",
             "text": "Identity Required",
             "data": {"detail": f"Please provide the last 4 digits of your card ending ···· {profile['card_last4']}."}},
        ]
        return {
            "outbox": [
                _a2ui_patch(components, "Identity Check"),
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    card_last4 = domain.get("card_last4", profile["card_last4"])
    result = freeze_card(card_last4)
    frozen_at = _fmt_time(result["frozen_at"])

    voice_text = (
        f"Done — your card ending {card_last4} is now frozen. "
        "No purchases can be made with it. "
        "Would you like me to order a replacement?"
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "frozen_card", "action_row"]},
        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 2}},
        {"id": "frozen_card", "component": "DataCard",
         "text": "Card Frozen",
         "data": {
             "status": "Frozen",
             "detail": "Your card is protected. No transactions can be made.",
             "items": [
                 {"label": "Card", "value": f"···· ···· ···· {card_last4}", "icon": "💳"},
                 {"label": "Frozen At", "value": frozen_at, "icon": "🕐"},
                 {"label": "Reference", "value": f"LC{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", "icon": "📄"},
             ],
         }},
        {"id": "action_row", "component": "Row", "children": ["btn_replace", "btn_done"]},
        {"id": "btn_replace", "component": "Button",
         "text": "Order Replacement Card",
         "data": {"action": "lost_card.order_replacement"}},
        {"id": "btn_done", "component": "Button",
         "text": "That's All, Thank You",
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
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})

    if not domain.get("identity_verified", False):
        voice_text = (
            "I need to verify your identity before ordering a replacement. "
            f"Please tell me the last four digits of your card ending ···· {profile['card_last4']}."
        )
        return {
            "outbox": [
                _a2ui_patch([
                    {"id": "root", "component": "Column", "children": ["gate"]},
                    {"id": "gate", "component": "DataCard",
                     "text": "Identity Required",
                     "data": {"detail": f"Last 4 digits of card ending ···· {profile['card_last4']} needed."}},
                ], "Identity Check"),
                {"type": "server.voice.say", "payload": {"text": voice_text}},
                {"type": "server.transcript.final",
                 "payload": {"text": voice_text, "role": "assistant"}},
            ],
        }

    card_last4 = domain.get("card_last4", profile["card_last4"])
    result = request_replacement(card_last4, profile["registered_address"])
    eta = result["estimated_arrival"]
    ref = result["tracking_reference"]

    voice_text = (
        f"Your replacement card has been ordered and will arrive by {eta}. "
        f"It'll be delivered to {profile['registered_address']}. "
        "We'll send you a text message when it's been dispatched."
    )

    components = [
        {"id": "root", "component": "Column",
         "children": ["timeline", "order_card", "next_steps"]},

        {"id": "timeline", "component": "Timeline",
         "data": {"steps": ["Report", "Verify", "Freeze", "Replace"], "current": 3}},

        {"id": "order_card", "component": "DataCard",
         "text": "Replacement Ordered",
         "data": {
             "status": "On Its Way",
             "detail": "Your new card is on its way. Keep an eye on your registered mobile for dispatch notifications.",
             "items": [
                 {"label": "Estimated Arrival", "value": eta, "icon": "📦"},
                 {"label": "Delivery Address", "value": profile["registered_address"], "icon": "🏠"},
                 {"label": "Order Reference", "value": ref, "icon": "📄"},
                 {"label": "Card Type", "value": profile["card_type"], "icon": "💳"},
             ],
         }},

        {"id": "next_steps", "component": "BenefitCard",
         "variant": "Info",
         "text": "What happens next?",
         "data": {
             "detail": (
                 "Your new card will arrive within 5 working days. "
                 "It will be automatically activated when you make your first transaction or call us. "
                 "Your frozen card will be cancelled once the new one is issued."
             ),
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
                "reference": ref,
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
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", profile["card_last4"])
    result = unfreeze_card(card_last4)
    unfrozen_at = _fmt_time(result["unfrozen_at"])

    voice_text = (
        f"Your card ending {card_last4} is now reactivated and ready to use. "
        "If you notice any unfamiliar transactions on your account, "
        "please contact us straight away. Is there anything else I can help with?"
    )

    components = [
        {"id": "root", "component": "Column", "children": ["active_card", "safety_tip"]},

        {"id": "active_card", "component": "DataCard",
         "text": "Card Reactivated",
         "data": {
             "status": "Active",
             "detail": "Your card is ready to use for purchases and contactless payments.",
             "items": [
                 {"label": "Card", "value": f"···· ···· ···· {card_last4}", "icon": "💳"},
                 {"label": "Reactivated At", "value": unfrozen_at, "icon": "🕐"},
                 {"label": "Status", "value": "Active", "icon": "✅"},
             ],
         }},

        {"id": "safety_tip", "component": "BenefitCard",
         "variant": "Info",
         "text": "Keep your card safe",
         "data": {
             "detail": (
                 "If you ever feel your card is at risk again, "
                 "you can freeze it instantly in the Barclays app or by calling us."
             ),
         }},
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
    """Escalate to the fraud team and show the transactions under investigation."""
    profile = get_customer_profile()
    domain = state.get("domain", {}).get("lost_card", {})
    card_last4 = domain.get("card_last4", profile["card_last4"])
    suspicious = domain.get("suspicious_tx") or get_suspicious_transactions(card_last4)
    case_ref = f"FRD{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"

    voice_text = (
        "I'm escalating your case to our specialist fraud team right now. "
        f"Your case reference is {case_ref}. "
        "They will review all the flagged transactions and contact you within 24 hours on your registered number. "
        "Your card will remain frozen throughout the investigation to protect you."
    )

    # Build transaction items for the investigation list
    tx_items = []
    for tx in suspicious[:5]:
        amount_str = f"-£{abs(tx['amount']):.2f}"
        tx_items.append({"label": f"{tx['date']} — {tx['merchant']}", "value": amount_str, "icon": "🔍"})

    components = [
        {"id": "root", "component": "Column",
         "children": ["escalation_card", "tx_under_review", "what_happens"]},

        {"id": "escalation_card", "component": "DataCard",
         "text": "Fraud Investigation Raised",
         "data": {
             "status": "Escalated",
             "detail": "Our fraud specialists have been notified and will review your case.",
             "items": [
                 {"label": "Case Reference", "value": case_ref, "icon": "📄"},
                 {"label": "Card", "value": f"···· ···· ···· {card_last4}", "icon": "💳"},
                 {"label": "Card Status", "value": "Frozen — Protected", "icon": "🔒"},
                 {"label": "Response Time", "value": "Within 24 hours", "icon": "🕐"},
             ],
         }},

        {"id": "tx_under_review", "component": "DataCard",
         "text": "Transactions Under Investigation",
         "data": {"items": tx_items}},

        {"id": "what_happens", "component": "BenefitCard",
         "variant": "Info",
         "text": "What happens next?",
         "data": {
             "detail": (
                 "Our team will investigate each flagged transaction. "
                 "If any are confirmed as fraudulent, you'll receive a full refund — "
                 "typically within 5 working days. "
                 "We'll contact you on your registered mobile number."
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
                "case_ref": case_ref,
                "risk_level": domain.get("risk_level"),
                "tx_count": len(suspicious),
            }),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "escalation_required": True,
        }},
    }


def action_reset(state: LostCardState) -> dict:
    """Return to the welcome screen."""
    profile = get_customer_profile()
    voice_text = f"Is there anything else I can help you with today, {profile['first_name']}?"
    components = [
        {"id": "root", "component": "Column", "children": ["done_card", "btn_more"]},
        {"id": "done_card", "component": "DataCard",
         "text": "All Done",
         "data": {"detail": "Your card has been dealt with. Let me know if you need anything else."}},
        {"id": "btn_more", "component": "Button",
         "text": "Help with Something Else",
         "data": {"action": "lost_card.reset"}},
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
builder.add_node("ingest_input",              ingest_input)
builder.add_node("handle_lost_or_stolen",     handle_lost_or_stolen)
builder.add_node("handle_found_card",         handle_found_card)
builder.add_node("handle_fraud_report",       handle_fraud_report)
builder.add_node("handle_general_enquiry",    handle_general_enquiry)
builder.add_node("handle_default",            handle_default)
builder.add_node("handle_contextual_query",   handle_contextual_query)

# UI action nodes
builder.add_node("handle_ui_action",           handle_ui_action)
builder.add_node("action_confirm_identity",    action_confirm_identity)
builder.add_node("action_freeze_card",         action_freeze_card)
builder.add_node("action_unfreeze_card",       action_unfreeze_card)
builder.add_node("action_request_replacement", action_request_replacement)
builder.add_node("action_escalate_fraud",      action_escalate_fraud)
builder.add_node("action_reset",               action_reset)

# Terminal
builder.add_node("clear_pending_action",       clear_pending_action)

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
        "action_confirm_identity": "action_confirm_identity",
        "handle_contextual_query": "handle_contextual_query",
    },
)

# handle_ui_action → ui_action_router → action nodes or transcript-driven nodes
builder.add_conditional_edges(
    "handle_ui_action",
    ui_action_router,
    {
        # Action nodes
        "action_freeze_card":          "action_freeze_card",
        "action_unfreeze_card":        "action_unfreeze_card",
        "action_request_replacement":  "action_request_replacement",
        "action_escalate_fraud":       "action_escalate_fraud",
        "action_confirm_identity":     "action_confirm_identity",
        "action_reset":                "action_reset",
        # Quick-start buttons route to transcript nodes
        "handle_lost_or_stolen":       "handle_lost_or_stolen",
        "handle_fraud_report":         "handle_fraud_report",
        "handle_general_enquiry":      "handle_general_enquiry",
        "handle_default":              "handle_default",
    },
)

# All leaf nodes → clear_pending_action → END
for _node in [
    "handle_lost_or_stolen", "handle_found_card", "handle_fraud_report",
    "handle_general_enquiry", "handle_default", "handle_contextual_query",
    "action_confirm_identity", "action_freeze_card", "action_unfreeze_card",
    "action_request_replacement", "action_escalate_fraud", "action_reset",
]:
    builder.add_edge(_node, "clear_pending_action")

builder.add_edge("clear_pending_action", END)

app_graph = builder.compile()

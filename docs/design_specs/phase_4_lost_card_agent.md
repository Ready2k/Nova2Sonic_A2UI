# Phase 4 Design Spec — Lost Card Agent (Full Implementation)

**Depends on:** Phase 1 and Phase 2 complete (Phase 3 not required)
**Branch suggestion:** `feat/phase-4-lost-card-agent`
**Estimated files changed:** 4 (all in `plugins/lost_card/`)

## Objective

Replace the Phase 2 stub graph with a production-quality Lost Card agent. The agent:
- Classifies the user's situation (lost card, fraud, found card, replacement, escalation)
- Requires identity verification before executing irreversible actions
- Emits audit events for every critical action
- Provides contextual A2UI screens at each step
- Keeps all actions under the `lost_card.*` namespace

No changes to `main.py`, `models.py`, or client code.

---

## Pre-flight Check

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# Phase 2 acceptance check
python -c "
from app.agent.plugins.lost_card.plugin import LostCardPlugin
p = LostCardPlugin()
s = p.create_initial_state()
assert s['domain']['lost_card']['card_status'] == 'active'
print('Phase 2 base: OK')
"
```

---

## Domain State (Final)

`state["domain"]["lost_card"]` is a dict with these keys.
`LostCardDomain` is documented in `graph.py` as a TypedDict but stored as a plain dict.

```python
class LostCardDomain(TypedDict):
    card_status: str            # "active" | "pending_freeze" | "frozen" | "cancelled"
    card_last4: Optional[str]   # last 4 digits, captured during identity step
    freeze_confirmed: bool      # user has confirmed they want to freeze
    replacement_requested: bool
    replacement_eta: Optional[str]
    identity_verified: bool     # True once user passes identity step
    risk_level: str             # "low" | "medium" | "high"
    suspicious_tx: List[Dict]   # populated by get_recent_transactions
    branch_requested: bool
    escalation_required: bool
    audit_log: List[Dict]       # append-only list of critical action records
```

Add `audit_log: []` to the initial domain state in `plugin.py`.

---

## Graph Node Specification

### Full Node List

```
ingest_input
  ↓ start_router
  ├── handle_lost_or_stolen   (transcript: lost/stolen keyword)
  ├── handle_found_card       (transcript: found keyword)
  ├── handle_fraud_report     (transcript: suspicious/fraud keyword)
  ├── handle_general_enquiry  (transcript: replacement, branch, other)
  ├── handle_default          (no match)
  └── handle_ui_action        (pendingAction present)
        ↓ ui_action_router
        ├── action_freeze_card
        ├── action_request_replacement
        ├── action_unfreeze_card
        ├── action_escalate_fraud
        ├── action_confirm_identity
        └── action_reset
clear_pending_action → END
```

Every path ends at `clear_pending_action` before `END`.

---

## Security & Policy Helpers

Add `_pii_safe.py` alongside `graph.py` **or** inline the functions in `graph.py`.
Inline is preferred (no new files).

### `_redact_card(text: str) -> str`

Mask all sequences of 13–19 consecutive digits in log strings.

```python
import re

_CARD_RE = re.compile(r'\b(\d{4}[\s-]?){3,4}\d{1,4}\b')

def _redact_card(text: str) -> str:
    return _CARD_RE.sub("****-****-****-****", str(text))
```

Use this in every `logger.*` call that could include card numbers or transcript text.

### `_audit_event(action: str, data: dict) -> dict`

Returns a `server.audit.event` outbox entry. `process_outbox` in `main.py` silently
drops this type (it is not forwarded to the client). A future audit hook can intercept it.

```python
from datetime import datetime, timezone

def _audit_event(action: str, data: dict) -> dict:
    return {
        "type": "server.audit.event",
        "payload": {
            "action": action,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
```

**Add to `process_outbox` in `main.py`:**

In the "Pass 1" loop that sends non-voice events, skip audit events:

```python
        for event in outbox:
            if event["type"] in ("server.voice.say", "server.audit.event"):
                continue          # audit events never forwarded to client
            ...
```

### Identity Verification Gate

Irreversible actions (`freeze_card`, `request_replacement`, `escalate_fraud`) must
check `domain["lost_card"]["identity_verified"]` before calling the tool.
If not verified, route to `request_identity_step` instead.

```python
def _requires_identity(state: dict, action: str) -> bool:
    """Return True if the action requires identity verification first."""
    irreversible = {"lost_card.freeze_card", "lost_card.order_replacement", "lost_card.escalate_fraud"}
    if action not in irreversible:
        return False
    domain = state.get("domain", {}).get("lost_card", {})
    return not domain.get("identity_verified", False)
```

---

## Node Implementations

### `ingest_input`

Pass-through node, identical to Phase 2 stub.

```python
def ingest_input(state: LostCardState) -> dict:
    return {}
```

### `classify_intent` (replaces Phase 2 inline routing logic)

Extracted as a separate helper, not a graph node, called from `start_router`.

```python
_LOST_KW      = {"lost", "stolen", "missing", "can't find", "cannot find", "lose", "gone"}
_FOUND_KW     = {"found", "found it", "located", "turns out", "still have"}
_FRAUD_KW     = {"suspicious", "fraud", "fraudulent", "don't recognise", "don't recognize",
                 "didn't make", "unauthorised", "unauthorized", "strange transaction",
                 "transactions i don't", "didn't authorise"}
_REPLACE_KW   = {"replacement", "new card", "replace", "order a card", "send me a card"}
_BRANCH_KW    = {"branch", "in person", "visit", "nearest", "come in"}

def classify_intent(transcript: str) -> str:
    t = transcript.lower()
    if any(k in t for k in _FRAUD_KW):    return "fraud"
    if any(k in t for k in _FOUND_KW):   return "found"
    if any(k in t for k in _LOST_KW):    return "lost"
    if any(k in t for k in _REPLACE_KW): return "replacement"
    if any(k in t for k in _BRANCH_KW):  return "branch"
    return "unknown"
```

### `start_router`

```python
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
```

### `handle_lost_or_stolen`

```python
def handle_lost_or_stolen(state: LostCardState) -> dict:
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
```

### `handle_fraud_report`

```python
def handle_fraud_report(state: LostCardState) -> dict:
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
```

### `handle_found_card`

```python
def handle_found_card(state: LostCardState) -> dict:
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
```

### `handle_general_enquiry`

Handles replacement card requests, branch finding, and other service queries.

```python
def handle_general_enquiry(state: LostCardState) -> dict:
    transcript = (state.get("transcript") or "").lower()
    domain = state.get("domain", {}).get("lost_card", {})
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
            {"id": "root", "component": "Column",
             "children": ["options_card"]},
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
```

### `handle_default`

```python
def handle_default(state: LostCardState) -> dict:
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
```

### `handle_ui_action` + `ui_action_router`

The dispatcher node reads `pendingAction` and routes to the correct action node.

```python
def handle_ui_action(state: LostCardState) -> dict:
    """Pass-through — routing in ui_action_router."""
    return {}


def ui_action_router(state: LostCardState) -> str:
    action = state.get("pendingAction") or {}
    action_id = (action.get("data") or {}).get("action") or action.get("id", "")

    routing = {
        "lost_card.freeze_card":         "action_freeze_card",
        "lost_card.unfreeze_card":       "action_unfreeze_card",
        "lost_card.order_replacement":   "action_request_replacement",
        "lost_card.escalate_fraud":      "action_escalate_fraud",
        "lost_card.confirm_identity":    "action_confirm_identity",
        "lost_card.reset":               "action_reset",
    }

    return routing.get(action_id, "handle_default")
```

### `action_confirm_identity`

Called when the user provides their card last 4 digits.

```python
def action_confirm_identity(state: LostCardState) -> dict:
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
        # After identity confirmed, auto-route to freeze
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

    # Chain into freeze automatically if identity succeeded
    if next_action:
        outbox.append({"type": "server.internal.chain_action",
                       "payload": {"action": next_action}})

    return {
        "outbox": outbox,
        "domain": {**state.get("domain", {}), "lost_card": domain_update},
    }
```

Note: `server.internal.chain_action` is dropped by `process_outbox` (add to the skip list
alongside `server.audit.event`). The freeze will be triggered on the user's next interaction.
For Phase 4 a simpler approach: after identity confirmation the graph auto-calls
`action_freeze_card` in the same turn (see routing note at end of this section).

### `action_freeze_card`

```python
def action_freeze_card(state: LostCardState) -> dict:
    domain = state.get("domain", {}).get("lost_card", {})

    # Identity gate
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
            _audit_event("freeze_card", {"card_last4": card_last4, "success": result["success"]}),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "frozen",
            "freeze_confirmed": True,
        }},
    }
```

### `action_request_replacement`

```python
def action_request_replacement(state: LostCardState) -> dict:
    domain = state.get("domain", {}).get("lost_card", {})

    if not domain.get("identity_verified", False):
        # Same identity gate pattern as freeze_card
        voice_text = "Please confirm your identity first by telling me your card's last 4 digits."
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
                "card_last4": card_last4,
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
```

### `action_unfreeze_card`

```python
def action_unfreeze_card(state: LostCardState) -> dict:
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
            _audit_event("unfreeze_card", {"card_last4": card_last4}),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "card_status": "active",
        }},
    }
```

### `action_escalate_fraud`

```python
def action_escalate_fraud(state: LostCardState) -> dict:
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
            _audit_event("escalate_fraud", {"card_last4": card_last4, "risk_level": domain.get("risk_level")}),
        ],
        "domain": {**state.get("domain", {}), "lost_card": {
            **domain,
            "escalation_required": True,
        }},
    }
```

### `action_reset`

```python
def action_reset(state: LostCardState) -> dict:
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
```

### `clear_pending_action`

```python
def clear_pending_action(state: LostCardState) -> dict:
    return {"pendingAction": None, "transcript": ""}
```

---

## Graph Assembly

```python
builder = StateGraph(LostCardState)

# Core nodes
builder.add_node("ingest_input",                  ingest_input)
builder.add_node("handle_lost_or_stolen",         handle_lost_or_stolen)
builder.add_node("handle_found_card",             handle_found_card)
builder.add_node("handle_fraud_report",           handle_fraud_report)
builder.add_node("handle_general_enquiry",        handle_general_enquiry)
builder.add_node("handle_default",                handle_default)

# UI action nodes
builder.add_node("handle_ui_action",              handle_ui_action)
builder.add_node("action_confirm_identity",       action_confirm_identity)
builder.add_node("action_freeze_card",            action_freeze_card)
builder.add_node("action_unfreeze_card",          action_unfreeze_card)
builder.add_node("action_request_replacement",    action_request_replacement)
builder.add_node("action_escalate_fraud",         action_escalate_fraud)
builder.add_node("action_reset",                  action_reset)

# Terminal
builder.add_node("clear_pending_action",          clear_pending_action)

# Edges
builder.add_edge(START, "ingest_input")
builder.add_conditional_edges(
    "ingest_input",
    start_router,
    {
        "handle_lost_or_stolen":         "handle_lost_or_stolen",
        "handle_found_card":             "handle_found_card",
        "handle_fraud_report":           "handle_fraud_report",
        "handle_general_enquiry":        "handle_general_enquiry",
        "handle_default":                "handle_default",
        "handle_ui_action":              "handle_ui_action",
    },
)

builder.add_conditional_edges(
    "handle_ui_action",
    ui_action_router,
    {
        "action_freeze_card":            "action_freeze_card",
        "action_unfreeze_card":          "action_unfreeze_card",
        "action_request_replacement":    "action_request_replacement",
        "action_escalate_fraud":         "action_escalate_fraud",
        "action_confirm_identity":       "action_confirm_identity",
        "action_reset":                  "action_reset",
        "handle_default":                "handle_default",
    },
)

# All paths → clear_pending_action → END
for node in [
    "handle_lost_or_stolen", "handle_found_card", "handle_fraud_report",
    "handle_general_enquiry", "handle_default", "action_confirm_identity",
    "action_freeze_card", "action_unfreeze_card", "action_request_replacement",
    "action_escalate_fraud", "action_reset",
]:
    builder.add_edge(node, "clear_pending_action")

builder.add_edge("clear_pending_action", END)

app_graph = builder.compile()
```

---

## Update `main.py` — Drop Audit Events from `process_outbox`

In the `process_outbox` function, "Pass 1" loop (approximately line 141):

**Before:**
```python
        for event in outbox:
            if event["type"] != "server.voice.say":
```

**After:**
```python
        _SKIP_TYPES = {"server.voice.say", "server.audit.event", "server.internal.chain_action"}
        for event in outbox:
            if event["type"] in _SKIP_TYPES:
```

---

## Acceptance Test Scenarios

### Scenario A — Happy Path: Lost Card → Freeze → Replace

```
User: "I've lost my card"
  → UI: Report Lost Card screen (Timeline step 0, DataCard status=Action Required)
  → Voice: "I can freeze your card immediately..."

User: "1234" (last 4 digits)
  → UI: Identity Confirmed
  → Auto-chains to freeze

UI Action: lost_card.freeze_card (with identity_verified=True)
  → UI: Card Frozen screen (Timeline step 2, status=Frozen)
  → Voice: "Done — your card ending 1234 is now frozen..."
  → Audit event emitted

UI Action: lost_card.order_replacement
  → UI: Replacement Ordered (Timeline step 3, status=On Its Way)
  → Voice: "Your replacement card will arrive by [date]"
  → Audit event emitted
```

### Scenario B — Fraud Path: Suspicious Transactions → Escalate

```
User: "I see transactions I don't recognise"
  → UI: Fraud Alert screen (BenefitCard Warning, identity_prompt)
  → Voice: "That's very concerning..."
  → state.domain.lost_card.risk_level = "high"

User: "5678"
  → identity_verified = True, card_last4 = "5678"

UI Action: lost_card.escalate_fraud
  → UI: Fraud Escalated (status=Escalated, 24h callback message)
  → Audit event emitted
```

### Scenario C — Recovery: Found Card After Freeze

```
UI Action: lost_card.freeze_card (assume identity already verified)
  → card_status = "frozen"

User: "Actually I found my card"
  → UI: Card Found screen (btn "Unfreeze My Card")
  → Voice: "Great news — your card is currently frozen..."

UI Action: lost_card.unfreeze_card
  → UI: Card Active (status=Active)
  → Audit event emitted
```

### Scenario D — Identity Gate Enforced

```
User: "I've lost my card"
  → UI: Report Lost Card (identity_verified=False)

UI Action: lost_card.freeze_card (sent without identity step)
  → identity gate fires
  → Voice: "For your security, I need to verify..."
  → card_status remains "pending_freeze"
```

---

## Verification

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# Graph import
python -c "from app.agent.plugins.lost_card.graph import app_graph; print('OK')"

# Scenario A — lost card → freeze (identity pre-set)
python -c "
from app.agent.plugins.lost_card.plugin import LostCardPlugin
p = LostCardPlugin()
s = p.create_initial_state()
s['transcript'] = \"I've lost my card\"
result = p.build_graph().invoke(s)
voice = [e for e in result['outbox'] if e['type'] == 'server.voice.say']
assert voice, 'No voice on lost card'
print('Lost path voice:', voice[0]['payload']['text'][:60])

# Freeze with identity pre-set
s2 = p.create_initial_state()
s2['pendingAction'] = {'id': 'btn_freeze', 'data': {'action': 'lost_card.freeze_card'}}
s2['domain']['lost_card']['identity_verified'] = True
s2['domain']['lost_card']['card_last4'] = '1234'
r2 = p.build_graph().invoke(s2)
audit = [e for e in r2['outbox'] if e['type'] == 'server.audit.event']
assert audit, 'No audit event on freeze'
frozen_status = r2['domain']['lost_card']['card_status']
assert frozen_status == 'frozen', f'Expected frozen, got {frozen_status}'
print('Freeze path: OK, audit events:', len(audit))
"

# Scenario D — identity gate
python -c "
from app.agent.plugins.lost_card.plugin import LostCardPlugin
p = LostCardPlugin()
s = p.create_initial_state()
# identity_verified defaults to False
s['pendingAction'] = {'id': 'btn_freeze', 'data': {'action': 'lost_card.freeze_card'}}
result = p.build_graph().invoke(s)
voice = [e for e in result['outbox'] if e['type'] == 'server.voice.say']
assert 'identity' in voice[0]['payload']['text'].lower() or 'security' in voice[0]['payload']['text'].lower()
assert result['domain']['lost_card']['card_status'] != 'frozen', 'Should not freeze without identity'
print('Identity gate: OK')
"
```

---

## Acceptance Criteria (Definition of Done)

- [ ] Happy path (Scenario A) works end-to-end
- [ ] Fraud path (Scenario B) sets `risk_level=high` and emits audit event on escalation
- [ ] Recovery path (Scenario C) correctly detects frozen status and offers unfreeze
- [ ] Identity gate (Scenario D) blocks `freeze_card` and `request_replacement` when `identity_verified=False`
- [ ] Every critical action (`freeze_card`, `request_replacement`, `escalate_fraud`, `unfreeze_card`) emits a `server.audit.event`
- [ ] Audit events do NOT reach the client (dropped in `process_outbox`)
- [ ] Card numbers are NOT logged in plaintext (uses `_redact_card`)
- [ ] All actions use `lost_card.*` namespace — no bare action IDs
- [ ] Mortgage flow completely unaffected

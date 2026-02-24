"""
scenarios_lost_card.py — Lost Card agent goal-based integration tests.

Requires server running on :8000 (./manage.sh start or uvicorn app.main:app --port 8000).

Run:
    cd tests && python run_tests.py --agent lost_card
    cd tests && python run_tests.py LC-01
    cd tests && python run_tests.py --list
"""

import asyncio
import time
from harness import TestClient, TestResult

LC_WS_URL = "ws://localhost:8000/ws?agent=lost_card"


# ─────────────────────────────────────────────────────────────────────────────
# LC-01 — Lost Card Happy Path: report → freeze → replace
# ─────────────────────────────────────────────────────────────────────────────

async def lc_01() -> TestResult:
    r = TestResult(
        "LC-01",
        "Lost card happy path: report lost → identity → freeze → order replacement",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Initial state check
            landing = c.get_a2ui_patches()
            r.check("Agent emits initial UI patch on connect", len(landing) > 0,
                    f"{len(landing)} patches")

            # Report lost card
            msgs = await c.say("I've lost my card")
            patches = c.get_a2ui_patches(msgs)
            r.check("Screen updates after reporting lost card", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent acknowledges lost card verbally", len(voice) > 0,
                    f"voice: {voice[0][:60] if voice else 'none'}")

            # UI action: freeze (with pre-verified identity via action data)
            freeze_msgs = await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card",
                 "card_last4": "1234",
                 "_test_bypass_identity": True},
            )
            freeze_patches = c.get_a2ui_patches(freeze_msgs)
            r.check("Freeze action produces UI update", len(freeze_patches) > 0)
            freeze_voice = c.get_transcripts(freeze_msgs)
            r.check("Agent confirms card frozen", len(freeze_voice) > 0,
                    freeze_voice[0][:60] if freeze_voice else "none")
            r.check("Freeze confirmation mentions 'frozen' or 'freeze'",
                    any("frozen" in t.lower() or "freeze" in t.lower()
                        for t in freeze_voice))

            # UI action: request replacement
            replace_msgs = await c.ui_action(
                "btn_replace",
                {"action": "lost_card.order_replacement"},
            )
            replace_patches = c.get_a2ui_patches(replace_msgs)
            r.check("Replacement action produces UI update", len(replace_patches) > 0)
            replace_voice = c.get_transcripts(replace_msgs)
            r.check("Agent confirms replacement ordered", len(replace_voice) > 0)
            r.check("Replacement confirmation mentions arrival date",
                    any("arrive" in t.lower() or "arrival" in t.lower() or "days" in t.lower()
                        for t in replace_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-02 — Fraud Path: suspicious transactions → escalate
# ─────────────────────────────────────────────────────────────────────────────

async def lc_02() -> TestResult:
    r = TestResult(
        "LC-02",
        "Fraud path: suspicious transactions → escalate to fraud team",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Report suspicious transactions
            msgs = await c.say("I see transactions I don't recognise")
            patches = c.get_a2ui_patches(msgs)
            r.check("Fraud report produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to fraud report verbally", len(voice) > 0)
            r.check("Response acknowledges fraud concern",
                    any("concern" in t.lower() or "fraud" in t.lower()
                        or "unauthorised" in t.lower() or "unauthorized" in t.lower()
                        for t in voice))

            # Escalate
            escalate_msgs = await c.ui_action(
                "btn_escalate",
                {"action": "lost_card.escalate_fraud"},
            )
            escalate_patches = c.get_a2ui_patches(escalate_msgs)
            r.check("Escalation produces UI update", len(escalate_patches) > 0)
            escalate_voice = c.get_transcripts(escalate_msgs)
            r.check("Agent confirms escalation verbally", len(escalate_voice) > 0)
            r.check("Escalation message mentions fraud team or specialist",
                    any("fraud team" in t.lower() or "specialist" in t.lower()
                        or "investigate" in t.lower()
                        for t in escalate_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-03 — Recovery Path: found card after freeze
# ─────────────────────────────────────────────────────────────────────────────

async def lc_03() -> TestResult:
    r = TestResult(
        "LC-03",
        "Recovery path: freeze card → report found → unfreeze",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Freeze first
            await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card", "card_last4": "5678",
                 "_test_bypass_identity": True},
            )

            # Report found
            msgs = await c.say("I found my card")
            patches = c.get_a2ui_patches(msgs)
            r.check("Found card report produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to found card", len(voice) > 0)
            r.check("Response mentions card is frozen (and offers unfreeze)",
                    any("frozen" in t.lower() or "reactivat" in t.lower()
                        or "unfreeze" in t.lower()
                        for t in voice))
            r.check("Unfreeze button present",
                    c.has_button_with_text("unfreeze", msgs)
                    or c.has_button_with_text("reactivat", msgs))

            # Unfreeze
            unfreeze_msgs = await c.ui_action(
                "btn_unfreeze",
                {"action": "lost_card.unfreeze_card"},
            )
            unfreeze_patches = c.get_a2ui_patches(unfreeze_msgs)
            r.check("Unfreeze produces UI update", len(unfreeze_patches) > 0)
            unfreeze_voice = c.get_transcripts(unfreeze_msgs)
            r.check("Agent confirms card reactivated", len(unfreeze_voice) > 0)
            r.check("Reactivation message mentions active status",
                    any("active" in t.lower() or "reactivat" in t.lower()
                        or "ready" in t.lower()
                        for t in unfreeze_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-04 — Identity Gate: freeze blocked without verification
# ─────────────────────────────────────────────────────────────────────────────

async def lc_04() -> TestResult:
    r = TestResult(
        "LC-04",
        "Identity gate: freeze attempt without identity verification is blocked",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Attempt to freeze without identity verification
            msgs = await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card"},  # no card_last4, no bypass
            )
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to premature freeze attempt", len(voice) > 0)
            r.check("Response mentions identity or security check",
                    any("identity" in t.lower() or "security" in t.lower()
                        or "verify" in t.lower() or "last four" in t.lower()
                        or "4 digit" in t.lower() or "digits" in t.lower()
                        for t in voice))

            # Card should NOT be frozen
            frozen_components = [
                c_ for c_ in c.get_all_components(msgs)
                if isinstance(c_.get("data"), dict)
                and "frozen" in str(c_.get("data", {}).get("status", "")).lower()
            ]
            r.check("Card is NOT frozen after blocked attempt",
                    len(frozen_components) == 0,
                    f"Found {len(frozen_components)} 'frozen' status components")

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-05 — Default Handler: unrecognised input
# ─────────────────────────────────────────────────────────────────────────────

async def lc_05() -> TestResult:
    r = TestResult(
        "LC-05",
        "Default handler: unrecognised input → helpful options menu",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            msgs = await c.say("Tell me about mortgages")
            patches = c.get_a2ui_patches(msgs)
            r.check("Default handler produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Default handler responds verbally", len(voice) > 0)
            r.check("Response guides user to card services",
                    any("card" in t.lower() or "lost" in t.lower() or "help" in t.lower()
                        for t in voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = {
    "LC-01": lc_01,
    "LC-02": lc_02,
    "LC-03": lc_03,
    "LC-04": lc_04,
    "LC-05": lc_05,
}

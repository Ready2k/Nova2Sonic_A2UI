"""
tools.py — Lost Card domain tool stubs.

These simulate banking API calls. Each function is idempotent.
Replace with real API calls in production; the interface is stable.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


# ── Mock customer profile ──────────────────────────────────────────────────────

_MOCK_PROFILE = {
    "first_name": "Sarah",
    "last_name": "Mitchell",
    "full_name": "Sarah Mitchell",
    "card_last4": "4821",
    "card_type": "Barclays Visa Debit",
    "card_expiry": "09/27",
    "registered_address": "14 Elmwood Close, Bristol, BS6 5AP",
    "phone": "•••• •••• 7342",
    "sort_code": "20-**-**",
    "account_number": "••••6714",
}


def get_customer_profile() -> Dict[str, Any]:
    """
    Return the authenticated customer's profile (stubbed).

    In production this is fetched from the core banking API using
    the session token. The interface is stable.
    """
    return _MOCK_PROFILE.copy()


# ── Card operations ────────────────────────────────────────────────────────────

def freeze_card(card_last4: str) -> Dict[str, Any]:
    """
    Simulate freezing a card.

    Idempotent — safe to call multiple times.
    """
    return {
        "success": True,
        "card_last4": card_last4,
        "status": "frozen",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Card ending {card_last4} has been frozen immediately.",
    }


def unfreeze_card(card_last4: str) -> Dict[str, Any]:
    """Simulate unfreezing a card (e.g. user found it)."""
    return {
        "success": True,
        "card_last4": card_last4,
        "status": "active",
        "unfrozen_at": datetime.now(timezone.utc).isoformat(),
        "message": f"Card ending {card_last4} has been reactivated.",
    }


def request_replacement(
    card_last4: str,
    delivery_address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Simulate ordering a replacement card.

    Idempotent — duplicate requests return the same ETA.
    delivery_address defaults to the registered address on file.
    """
    eta = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%A %-d %B")
    address = delivery_address or _MOCK_PROFILE["registered_address"]
    return {
        "success": True,
        "card_last4": card_last4,
        "replacement_requested": True,
        "estimated_arrival": eta,
        "delivery_address": address,
        "tracking_reference": f"BRC{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
        "message": f"A new card will arrive by {eta}.",
    }


# ── Transaction data ───────────────────────────────────────────────────────────

_RECENT_TRANSACTIONS: List[Dict[str, Any]] = [
    {"date": "26 Feb", "merchant": "GOOGLE*SVCS", "amount": -149.99, "currency": "GBP", "suspicious": True,
     "note": "International charge — country: US"},
    {"date": "25 Feb", "merchant": "AMAZON MKT EU", "amount": -89.00, "currency": "GBP", "suspicious": True,
     "note": "Online purchase — unfamiliar seller"},
    {"date": "25 Feb", "merchant": "Tesco Superstore", "amount": -42.30, "currency": "GBP", "suspicious": False},
    {"date": "24 Feb", "merchant": "TfL Travel", "amount": -6.80, "currency": "GBP", "suspicious": False},
    {"date": "24 Feb", "merchant": "WITHDRAWL — ATM UNKNOWN", "amount": -200.00, "currency": "GBP", "suspicious": True,
     "note": "ATM not in your usual area"},
    {"date": "23 Feb", "merchant": "Costa Coffee", "amount": -4.50, "currency": "GBP", "suspicious": False},
    {"date": "22 Feb", "merchant": "Sainsbury's", "amount": -67.14, "currency": "GBP", "suspicious": False},
]


def get_recent_transactions(card_last4: str) -> List[Dict[str, Any]]:
    """
    Return recent transactions for a card (stubbed data).

    In production this calls the transactions API.
    """
    return _RECENT_TRANSACTIONS.copy()


def get_suspicious_transactions(card_last4: str) -> List[Dict[str, Any]]:
    """Return only the flagged suspicious transactions."""
    return [t for t in _RECENT_TRANSACTIONS if t.get("suspicious")]

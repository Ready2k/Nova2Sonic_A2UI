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

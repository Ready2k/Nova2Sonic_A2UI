"""
persistence.py — Simple JSON-file persistence for the lost card domain state.

Stores domain state keyed by customer ID so a hard-refresh restores
the card's current status (frozen, replacement ordered, etc.).

For a production system this would be a database call using the
authenticated session token. The interface is stable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)

_STORE_PATH = os.path.join(os.path.dirname(__file__), "session_data.json")

# Single mock customer for the demo. In production, derived from auth token.
_CUSTOMER_KEY = "demo"

# Fields that are ephemeral and should NOT survive a hard refresh.
_EPHEMERAL_KEYS = {"suspicious_tx", "audit_log"}


def load_domain() -> Dict[str, Any]:
    """
    Load the persisted lost_card domain state for the current customer.
    Returns an empty dict if nothing has been saved yet.
    """
    try:
        if os.path.exists(_STORE_PATH):
            with open(_STORE_PATH, "r") as f:
                data = json.load(f)
            return data.get(_CUSTOMER_KEY, {})
    except Exception as exc:
        logger.warning("persistence: failed to load state — %s", exc)
    return {}


def save_domain(domain: Dict[str, Any]) -> None:
    """
    Persist the lost_card domain state, stripping ephemeral fields.
    Safe to call after every graph turn; writes are small and fast.
    """
    try:
        to_save = {k: v for k, v in domain.items() if k not in _EPHEMERAL_KEYS}

        existing: Dict[str, Any] = {}
        if os.path.exists(_STORE_PATH):
            with open(_STORE_PATH, "r") as f:
                existing = json.load(f)

        existing[_CUSTOMER_KEY] = to_save
        with open(_STORE_PATH, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as exc:
        logger.warning("persistence: failed to save state — %s", exc)


def clear_domain() -> None:
    """Wipe persisted state for the current customer (e.g. after card replacement)."""
    try:
        if os.path.exists(_STORE_PATH):
            with open(_STORE_PATH, "r") as f:
                existing = json.load(f)
            existing.pop(_CUSTOMER_KEY, None)
            with open(_STORE_PATH, "w") as f:
                json.dump(existing, f, indent=2)
    except Exception as exc:
        logger.warning("persistence: failed to clear state — %s", exc)

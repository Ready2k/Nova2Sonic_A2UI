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
        All mortgage-specific top-level keys preserved for Phase 1–2 compat.
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
            "domain": {},
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

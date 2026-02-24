"""
plugin.py — MortgagePlugin: wraps the existing LangGraph mortgage graph.

Phase 3 complete: all mortgage-specific state lives under domain["mortgage"].
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
        Phase 3 state shape: all mortgage-specific data lives under
        state["domain"]["mortgage"].  CommonState envelope keys are the
        only top-level keys shared across all plugins.
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
            "state_version": self.state_version,
            # ── Mortgage domain data ──────────────────────────────────────
            "domain": {
                "mortgage": {
                    # Group A
                    "branch_requested": False,
                    # Group B
                    "address_validation_failed": False,
                    "last_attempted_address": None,
                    # Group C
                    "trouble_count": 0,
                    "show_support": False,
                    # Group D
                    "existing_customer": None,
                    "property_seen": None,
                    "process_question": None,
                    # Group E
                    "intent": {
                        "propertyValue": None,
                        "loanBalance": None,
                        "fixYears": None,
                        "termYears": 25,
                    },
                    "ltv": 0.0,
                    "products": [],
                    "selection": {},
                },
            },
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

"""
plugin.py — LostCardPlugin.

Wraps the Lost Card LangGraph agent. State uses the CommonState envelope
with domain data under state["domain"]["lost_card"].
"""

from __future__ import annotations

from typing import Any, Dict

from app.agent.core.contracts import PluginBase


class LostCardPlugin(PluginBase):

    @property
    def plugin_id(self) -> str:
        return "lost_card"

    def build_graph(self):
        from app.agent.plugins.lost_card.graph import app_graph
        return app_graph

    def create_initial_state(self) -> Dict[str, Any]:
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
            # ── Domain data ───────────────────────────────────────────────
            "domain": {
                "lost_card": {
                    "card_status": "active",    # "active" | "frozen" | "cancelled"
                    "card_last4": None,          # populated when user provides it
                    "freeze_confirmed": False,
                    "replacement_requested": False,
                    "replacement_eta": None,
                    "identity_verified": False,
                    "risk_level": "low",         # "low" | "medium" | "high"
                    "suspicious_tx": [],
                    "branch_requested": False,
                    "escalation_required": False,
                    "audit_log": [],
                },
            },
        }

    @property
    def capabilities(self) -> Dict[str, Any]:
        return {
            "voice_greeting": "Hello, I can help you with your lost or stolen card.",
            "supported_components": [
                "Column", "Row", "DataCard", "Button", "Timeline", "BenefitCard",
            ],
        }

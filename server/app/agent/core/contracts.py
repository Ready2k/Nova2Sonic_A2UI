"""
contracts.py — Shared plugin interface and common state/event types.

Every domain plugin must implement PluginBase. The CommonState TypedDict
documents the envelope keys that the runtime (main.py, process_outbox) reads.
Domain-specific data lives under state["domain"][plugin_id].
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, TypedDict


# ── Common State ────────────────────────────────────────────────────────────────
#
# Phase 1–2: plugins keep mortgage-specific top-level keys for backward compat.
# Phase 3: those keys migrate under state["domain"]["mortgage"].
# Only the keys below are read by runtime code (main.py / process_outbox).

class CommonState(TypedDict, total=False):
    mode: str                           # "voice" | "text"
    device: str                         # "desktop" | "mobile"
    transcript: str                     # current utterance being processed
    messages: List[Dict[str, Any]]      # conversation history (append-reduced)
    ui: Dict[str, Any]                  # { surfaceId: str, state: str }
    errors: Optional[Dict[str, Any]]
    pendingAction: Optional[Dict[str, Any]]   # { id: str, data: dict }
    outbox: List[Dict[str, Any]]        # server events to flush (append-reduced)
    meta: Dict[str, Any]                # { session_id, agent_id, ... }
    domain: Dict[str, Any]             # plugin-owned payload keyed by plugin_id
    state_version: int                  # plugin schema version


# ── Server Event ─────────────────────────────────────────────────────────────────
#
# Shape of entries in state["outbox"]. Runtime reads only "type" and "payload".

class ServerEvent(TypedDict):
    type: str          # "server.a2ui.patch" | "server.voice.say" | etc.
    payload: Dict[str, Any]


# ── Plugin Interface ──────────────────────────────────────────────────────────────

class PluginBase(ABC):
    """
    Abstract base for all domain plugins.

    Implement plugin_id, create_initial_state, and build_graph.
    validate_action and capabilities have sensible defaults.
    """

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique slug, e.g. 'mortgage' or 'lost_card'."""
        ...

    @property
    def state_version(self) -> int:
        """Increment when the plugin's domain state shape changes."""
        return 1

    @abstractmethod
    def create_initial_state(self) -> Dict[str, Any]:
        """
        Return a fully-initialised state dict for a new session.

        Must include every key in CommonState plus any plugin-specific
        top-level keys needed during the Phase 1–2 compatibility window.
        """
        ...

    @abstractmethod
    def build_graph(self):
        """
        Return the plugin's compiled LangGraph CompiledStateGraph.

        Called once per graph invocation; implementations may cache.
        """
        ...

    def validate_action(
        self,
        action_id: str,
        data: Dict[str, Any],
    ) -> Optional[str]:
        """
        Validate and normalise a UI action ID arriving from the client.

        Returns the canonical action_id to use, or None to reject the action.
        Default implementation accepts everything.

        Phase 2+: override to enforce 'plugin_id.*' namespace.
        """
        return action_id

    def post_invoke(self, state: Dict[str, Any]) -> None:
        """
        Optional hook called by the runtime after every graph invocation.

        Override in plugins that need to persist state between sessions.
        The default implementation is a no-op.
        """

    @property
    def capabilities(self) -> Dict[str, Any]:
        """
        Optional metadata about what this plugin supports.

        Example keys: voice_greeting, supported_components, required_env_vars.
        Not used by the runtime; intended for tooling and documentation.
        """
        return {}

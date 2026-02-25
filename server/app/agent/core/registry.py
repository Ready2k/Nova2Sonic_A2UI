"""
registry.py — Plugin registration and lookup.

Plugins register themselves at import time. The runtime calls get_plugin()
with an agent_id string to retrieve the correct plugin for a session.
"""

from __future__ import annotations

import logging
from typing import Dict

from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)

# Module-level registry — populated by register() calls in main.py startup.
_registry: Dict[str, PluginBase] = {}


def register(plugin: PluginBase) -> None:
    """Register a plugin instance. Overwrites any existing entry for the same plugin_id."""
    _registry[plugin.plugin_id] = plugin
    logger.info("[AgentRegistry] Registered plugin: %s (state_version=%d)",
                plugin.plugin_id, plugin.state_version)


def get_plugin(agent_id: str) -> PluginBase:
    """
    Return the registered plugin for agent_id.

    Raises KeyError if agent_id is not registered, so callers can catch
    and close the WebSocket with a 4000 code (see main.py).
    """
    plugin = _registry.get(agent_id)
    if plugin is None:
        available = list(_registry.keys())
        raise KeyError(
            f"No plugin registered for agent_id={agent_id!r}. "
            f"Available: {available}"
        )
    return plugin


def list_plugins() -> list:
    """Return registered plugin IDs — useful for health checks."""
    return list(_registry.keys())

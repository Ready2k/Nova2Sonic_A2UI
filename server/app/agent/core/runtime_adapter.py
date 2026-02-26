"""
runtime_adapter.py — Thin async wrapper around graph.invoke.

Keeps the event loop unblocked by running the synchronous LangGraph
invoke on a thread pool via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.agent.core.contracts import PluginBase

logger = logging.getLogger(__name__)


async def invoke_graph(
    plugin: PluginBase,
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run plugin.build_graph().invoke(state, config) on a thread.

    Returns the new state dict produced by the graph.
    Propagates any exception raised by the graph (caller must handle).
    """
    graph = plugin.build_graph()
    logger.debug("[RuntimeAdapter] Invoking graph for plugin=%s", plugin.plugin_id)
    result = await asyncio.to_thread(graph.invoke, state, config)
    logger.debug("[RuntimeAdapter] Graph invoke complete for plugin=%s", plugin.plugin_id)
    plugin.post_invoke(result)
    return result

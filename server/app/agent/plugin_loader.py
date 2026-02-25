"""
plugin_loader.py — Auto-discovery and registration of all agent plugins.

Scans the app.agent.plugins package for any sub-package that contains a
`plugin.py` module exposing a PluginBase subclass, then registers one
instance of each.

Usage in main.py:
    from app.agent.plugin_loader import load_all_plugins
    load_all_plugins()
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from app.agent.core.contracts import PluginBase
from app.agent.core.registry import register

logger = logging.getLogger(__name__)


def load_all_plugins() -> list[str]:
    """
    Discover and register every plugin found under app.agent.plugins.

    Returns the list of plugin_ids that were successfully registered.
    Logs a warning (does not raise) for any plugin that fails to load.
    """
    import app.agent.plugins as _plugins_pkg

    registered: list[str] = []

    for _finder, name, _ispkg in pkgutil.iter_modules(_plugins_pkg.__path__):
        module_path = f"app.agent.plugins.{name}.plugin"
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            logger.warning(
                "[PluginLoader] Could not import %s: %s", module_path, exc
            )
            continue

        # Find the first concrete PluginBase subclass in the module.
        plugin_cls = None
        for _attr_name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                obj is not PluginBase
                and issubclass(obj, PluginBase)
                and obj.__module__ == module.__name__
            ):
                plugin_cls = obj
                break

        if plugin_cls is None:
            logger.warning(
                "[PluginLoader] No PluginBase subclass found in %s — skipping",
                module_path,
            )
            continue

        try:
            instance = plugin_cls()
            register(instance)
            registered.append(instance.plugin_id)
        except Exception as exc:
            logger.warning(
                "[PluginLoader] Failed to instantiate or register %s: %s",
                plugin_cls.__name__,
                exc,
            )

    logger.info("[PluginLoader] Loaded plugins: %s", registered)
    return registered

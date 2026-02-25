"""
generator.py — Generate plugin scaffold files from an InspectionResult.

Uses Jinja2 templates stored alongside this module in templates/.
Returns a dict of { filename → rendered_content } so callers can
either write to disk or return as a dry-run preview.
"""

from __future__ import annotations

import pprint
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.agent.core.importer.inspector import InspectionResult

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_VALID_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class GeneratorConfig:
    plugin_id: str
    plugin_class_name: str        # e.g. "MyAgentPlugin"
    external_module: str          # Python import path, e.g. "my_agent.agent"
    graph_export: str             # variable name in that module, e.g. "graph"
    input_field: str              # state key that holds user input, e.g. "messages"
    output_accessor: str          # Python expression to get response, e.g. "messages[-1].content"
    state_fields: List[dict]      # [{"name": ..., "annotation": ...}, ...]
    initial_domain_state: dict    # default values for domain[plugin_id]
    # Fallback A2UI screens (replaced by LLM output in Phase 2)
    screens: dict                 # {"welcome": {...}, "result": {...}}
    readme_excerpt: str = ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_plugin_id(plugin_id: str) -> None:
    if not _VALID_PLUGIN_ID.match(plugin_id):
        raise ValueError(
            f"Invalid plugin_id '{plugin_id}'. "
            "Must match ^[a-z][a-z0-9_]{{1,31}}$"
        )


# ---------------------------------------------------------------------------
# Default (Phase 1) A2UI screens
# These are minimal DataCard scaffolds.  Phase 2 replaces them with
# LLM-designed component trees.
# ---------------------------------------------------------------------------

def _default_screens(plugin_id: str, agent_name: str) -> dict:
    return {
        "welcome": {
            "title": agent_name,
            "voice_text": f"Hello! I'm your {agent_name} assistant. How can I help you today?",
            "components": [
                {"id": "root", "component": "Column", "children": ["welcome_card"]},
                {
                    "id": "welcome_card",
                    "component": "DataCard",
                    "text": agent_name,
                    "data": {
                        "detail": "How can I help you today? Type or speak your question.",
                    },
                },
            ],
        },
        "result": {
            "title": "Response",
            "voice_text": "{response}",   # placeholder — substituted at runtime
            "components": [
                {"id": "root", "component": "Column", "children": ["result_card"]},
                {
                    "id": "result_card",
                    "component": "DataCard",
                    "text": "Response",
                    "data": {"detail": "{response}"},  # substituted at runtime
                },
            ],
        },
        "error": {
            "title": "Something went wrong",
            "voice_text": "Sorry, something went wrong. Please try again.",
            "components": [
                {"id": "root", "component": "Column", "children": ["error_card"]},
                {
                    "id": "error_card",
                    "component": "DataCard",
                    "text": "Error",
                    "data": {
                        "detail": "Something went wrong. Please try again.",
                    },
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Build a GeneratorConfig from an InspectionResult
# ---------------------------------------------------------------------------

def config_from_inspection(
    inspection: InspectionResult,
    plugin_id: str,
    external_module: str,
    graph_export: str,
    readme_excerpt: str = "",
    screens: Optional[dict] = None,
    input_field_override: Optional[str] = None,
    output_accessor_override: Optional[str] = None,
    initial_domain_state_override: Optional[dict] = None,
) -> GeneratorConfig:
    """
    Build a GeneratorConfig from an InspectionResult.

    LLM overrides (input_field_override, output_accessor_override,
    initial_domain_state_override, screens) take precedence over
    inspector heuristics when provided.
    """
    validate_plugin_id(plugin_id)

    agent_name = " ".join(w.capitalize() for w in plugin_id.split("_"))
    plugin_class_name = agent_name.replace(" ", "") + "Plugin"

    state_fields = [
        {"name": f.name, "annotation": f.annotation}
        for f in inspection.state_fields
    ]

    # Default domain state: one null entry per detected state field
    # (minus common LangGraph envelope fields we don't want to duplicate).
    _SKIP_FIELDS = {"messages", "config", "configurable"}
    default_domain_state = {
        f.name: None
        for f in inspection.state_fields
        if f.name not in _SKIP_FIELDS
    }

    return GeneratorConfig(
        plugin_id=plugin_id,
        plugin_class_name=plugin_class_name,
        external_module=external_module,
        graph_export=graph_export,
        input_field=input_field_override or inspection.detected_input_field,
        output_accessor=output_accessor_override or inspection.detected_output_field,
        state_fields=state_fields,
        initial_domain_state=(
            initial_domain_state_override
            if initial_domain_state_override is not None
            else default_domain_state
        ),
        screens=screens or _default_screens(plugin_id, agent_name),
        readme_excerpt=readme_excerpt,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def render(config: GeneratorConfig) -> Dict[str, str]:
    """
    Render all plugin scaffold files.

    Returns { relative_filename → rendered_source }.
    The caller is responsible for writing these to disk.
    """
    validate_plugin_id(config.plugin_id)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    # Use pprint.pformat so Python literals (None, True, False) are written
    # instead of JSON literals (null, true, false).
    env.filters["to_python"] = lambda v: pprint.pformat(v, width=100)

    context = {
        "plugin_id": config.plugin_id,
        "plugin_class_name": config.plugin_class_name,
        "external_module": config.external_module,
        "graph_export": config.graph_export,
        "input_field": config.input_field,
        "output_accessor": config.output_accessor,
        "state_fields": config.state_fields,
        "initial_domain_state": config.initial_domain_state,
        "screens": config.screens,
        "readme_excerpt": config.readme_excerpt,
    }

    files: Dict[str, str] = {}

    for template_name, output_name in [
        ("plugin.py.j2",        "plugin.py"),
        ("graph_wrapper.py.j2", "graph.py"),
        ("init.py.j2",          "__init__.py"),
    ]:
        tmpl = env.get_template(template_name)
        files[output_name] = tmpl.render(**context)

    return files

"""
langgraph_json.py — Parse a langgraph.json config file.

langgraph.json format (LangGraph Cloud standard):
{
    "graphs": {
        "<graph_id>": "./<path>/<file>.py:<export_name>"
    },
    "dependencies": ["./my_agent"],
    "env": ".env"
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class GraphEntry:
    graph_id: str
    file_path: str       # e.g. "my_agent/agent.py"  (relative to repo root)
    export_name: str     # e.g. "graph"


@dataclass
class LangGraphConfig:
    graphs: List[GraphEntry]
    dependencies: List[str]          # relative paths to included packages
    env_file: Optional[str]          # path to .env, or None
    raw: dict = field(default_factory=dict)  # original parsed JSON


class LangGraphJsonError(ValueError):
    """Raised when langgraph.json is missing or malformed."""


def parse(repo_root: Path) -> LangGraphConfig:
    """
    Parse langgraph.json from repo_root.

    Raises LangGraphJsonError if the file is absent or malformed.
    """
    config_path = repo_root / "langgraph.json"
    if not config_path.exists():
        raise LangGraphJsonError(
            f"langgraph.json not found in {repo_root}. "
            "Is this a LangGraph project?"
        )

    try:
        with config_path.open() as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise LangGraphJsonError(f"langgraph.json is not valid JSON: {exc}") from exc

    graphs_raw = data.get("graphs")
    if not isinstance(graphs_raw, dict) or not graphs_raw:
        raise LangGraphJsonError(
            "langgraph.json must contain a non-empty 'graphs' dict."
        )

    graphs: List[GraphEntry] = []
    for graph_id, entry in graphs_raw.items():
        if not isinstance(entry, str):
            raise LangGraphJsonError(
                f"Graph entry for '{graph_id}' must be a string, got {type(entry)}"
            )
        if ":" in entry:
            file_part, export_name = entry.rsplit(":", 1)
        else:
            file_part, export_name = entry, "graph"

        # Normalise: strip leading "./" and OS separators
        file_path = file_part.lstrip("./").replace("\\", "/")
        graphs.append(GraphEntry(
            graph_id=graph_id,
            file_path=file_path,
            export_name=export_name.strip(),
        ))

    dependencies = [
        d.lstrip("./") for d in data.get("dependencies", [])
        if isinstance(d, str)
    ]

    return LangGraphConfig(
        graphs=graphs,
        dependencies=dependencies,
        env_file=data.get("env"),
        raw=data,
    )


def pick_graph(config: LangGraphConfig, graph_id: Optional[str] = None) -> GraphEntry:
    """
    Return the GraphEntry for graph_id, or the first entry if graph_id is None.

    Raises LangGraphJsonError if graph_id is not found.
    """
    if graph_id is None:
        return config.graphs[0]

    for entry in config.graphs:
        if entry.graph_id == graph_id:
            return entry

    available = [g.graph_id for g in config.graphs]
    raise LangGraphJsonError(
        f"Graph '{graph_id}' not found. Available graphs: {available}"
    )

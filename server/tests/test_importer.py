"""
Unit tests for the Agent Import System importer modules.

Covers:
  - langgraph_json: parse(), pick_graph()
  - inspector:      inspect_file(), inspect_graph_entry()
  - generator:      validate_plugin_id(), config_from_inspection(), render()
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from app.agent.core.importer import langgraph_json, inspector, generator
from app.agent.core.importer.langgraph_json import (
    LangGraphConfig,
    LangGraphJsonError,
    GraphEntry,
    parse,
    pick_graph,
)
from app.agent.core.importer.inspector import (
    InspectionError,
    InspectionResult,
    inspect_file,
    inspect_graph_entry,
)
from app.agent.core.importer.generator import (
    GeneratorConfig,
    config_from_inspection,
    render,
    validate_plugin_id,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ── langgraph_json ─────────────────────────────────────────────────────────────

class TestParse:
    def test_valid_graph(self, tmp_path):
        _write(tmp_path, "langgraph.json", """\
            {
                "graphs": {
                    "agent": "./my_agent/agent.py:graph"
                },
                "dependencies": ["./my_agent"]
            }
        """)
        config = parse(tmp_path)
        assert len(config.graphs) == 1
        g = config.graphs[0]
        assert g.graph_id == "agent"
        assert g.file_path == "my_agent/agent.py"
        assert g.export_name == "graph"
        assert config.dependencies == ["my_agent"]

    def test_strips_leading_dotslash(self, tmp_path):
        _write(tmp_path, "langgraph.json", """\
            {"graphs": {"a": "./sub/dir/file.py:app"}}
        """)
        config = parse(tmp_path)
        assert config.graphs[0].file_path == "sub/dir/file.py"

    def test_no_colon_defaults_to_graph(self, tmp_path):
        _write(tmp_path, "langgraph.json", """\
            {"graphs": {"a": "./agent.py"}}
        """)
        config = parse(tmp_path)
        assert config.graphs[0].export_name == "graph"

    def test_multiple_graphs(self, tmp_path):
        _write(tmp_path, "langgraph.json", """\
            {
                "graphs": {
                    "one": "./one.py:app1",
                    "two": "./two.py:app2"
                }
            }
        """)
        config = parse(tmp_path)
        assert len(config.graphs) == 2
        ids = [g.graph_id for g in config.graphs]
        assert "one" in ids
        assert "two" in ids

    def test_missing_file(self, tmp_path):
        with pytest.raises(LangGraphJsonError, match="not found"):
            parse(tmp_path)

    def test_invalid_json(self, tmp_path):
        _write(tmp_path, "langgraph.json", "{ not valid json }")
        with pytest.raises(LangGraphJsonError, match="not valid JSON"):
            parse(tmp_path)

    def test_empty_graphs(self, tmp_path):
        _write(tmp_path, "langgraph.json", '{"graphs": {}}')
        with pytest.raises(LangGraphJsonError, match="non-empty"):
            parse(tmp_path)

    def test_graphs_not_dict(self, tmp_path):
        _write(tmp_path, "langgraph.json", '{"graphs": ["./agent.py:g"]}')
        with pytest.raises(LangGraphJsonError, match="non-empty"):
            parse(tmp_path)

    def test_graph_entry_not_string(self, tmp_path):
        _write(tmp_path, "langgraph.json", '{"graphs": {"a": 123}}')
        with pytest.raises(LangGraphJsonError, match="must be a string"):
            parse(tmp_path)

    def test_env_file(self, tmp_path):
        _write(tmp_path, "langgraph.json", """\
            {"graphs": {"a": "./a.py:g"}, "env": ".env"}
        """)
        config = parse(tmp_path)
        assert config.env_file == ".env"

    def test_no_env_file(self, tmp_path):
        _write(tmp_path, "langgraph.json", '{"graphs": {"a": "./a.py:g"}}')
        config = parse(tmp_path)
        assert config.env_file is None


class TestPickGraph:
    def _config(self, graph_ids: list[str]) -> LangGraphConfig:
        return LangGraphConfig(
            graphs=[GraphEntry(g, f"{g}.py", "graph") for g in graph_ids],
            dependencies=[],
            env_file=None,
        )

    def test_no_id_returns_first(self):
        config = self._config(["alpha", "beta"])
        entry = pick_graph(config, None)
        assert entry.graph_id == "alpha"

    def test_pick_by_id(self):
        config = self._config(["alpha", "beta"])
        entry = pick_graph(config, "beta")
        assert entry.graph_id == "beta"

    def test_missing_id_raises(self):
        config = self._config(["alpha"])
        with pytest.raises(LangGraphJsonError, match="not found"):
            pick_graph(config, "missing")


# ── inspector ──────────────────────────────────────────────────────────────────

_SIMPLE_AGENT = """\
from typing import TypedDict, List
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, END, START

class AgentState(TypedDict):
    messages: List[BaseMessage]
    query: str
    output: str

builder = StateGraph(AgentState)

def ingest(state: AgentState) -> dict:
    return {}

def respond(state: AgentState) -> dict:
    return {"output": "hello"}

builder.add_node("ingest", ingest)
builder.add_node("respond", respond)
builder.add_edge(START, "ingest")
builder.add_edge("ingest", "respond")
builder.add_edge("respond", END)

graph = builder.compile()
"""

_PYDANTIC_AGENT = """\
from pydantic import BaseModel
from typing import TypedDict

class OutputSchema(BaseModel):
    answer: str
    confidence: float

class State(TypedDict):
    question: str
    result: str

graph = None  # placeholder
"""


class TestInspectFile:
    def test_state_class_detected(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        assert r.state_class == "AgentState"

    def test_state_fields(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        field_names = [sf.name for sf in r.state_fields]
        assert "messages" in field_names
        assert "query" in field_names
        assert "output" in field_names

    def test_nodes_detected(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        node_names = [n.name for n in r.nodes]
        assert "ingest" in node_names
        assert "respond" in node_names

    def test_compiled_export(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        assert r.compiled_export == "graph"

    def test_pydantic_models(self, tmp_path):
        f = _write(tmp_path, "agent.py", _PYDANTIC_AGENT)
        r = inspect_file(f)
        assert "OutputSchema" in r.pydantic_models

    def test_io_heuristic_messages(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        # "messages" should be detected as input field
        assert r.detected_input_field == "messages"
        assert "messages" in r.detected_output_field

    def test_io_heuristic_query(self, tmp_path):
        source = """\
from typing import TypedDict
class State(TypedDict):
    query: str
    answer: str
graph = None
"""
        f = _write(tmp_path, "agent.py", source)
        r = inspect_file(f)
        assert r.detected_input_field == "query"

    def test_no_typeddict_warns(self, tmp_path):
        f = _write(tmp_path, "agent.py", "graph = None\n")
        r = inspect_file(f)
        assert r.state_class is None
        assert any("TypedDict" in w for w in r.warnings)

    def test_multiple_typedicts_warns(self, tmp_path):
        source = """\
from typing import TypedDict
class StateA(TypedDict):
    x: str
class StateB(TypedDict):
    y: str
graph = None
"""
        f = _write(tmp_path, "agent.py", source)
        r = inspect_file(f)
        assert r.state_class == "StateA"
        assert any("Multiple TypedDict" in w for w in r.warnings)

    def test_annotated_reducer(self, tmp_path):
        source = """\
from typing import TypedDict, Annotated, List
import operator
class State(TypedDict):
    messages: Annotated[List[str], operator.add]
graph = None
"""
        f = _write(tmp_path, "agent.py", source)
        r = inspect_file(f)
        msgs_field = next(sf for sf in r.state_fields if sf.name == "messages")
        assert msgs_field.has_reducer is True

    def test_source_snippet(self, tmp_path):
        f = _write(tmp_path, "agent.py", _SIMPLE_AGENT)
        r = inspect_file(f)
        assert len(r.source_snippet) > 0
        assert len(r.source_snippet) <= 3000

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(InspectionError, match="Cannot read"):
            inspect_file(tmp_path / "nonexistent.py")

    def test_syntax_error_raises(self, tmp_path):
        f = _write(tmp_path, "bad.py", "def broken(:\n    pass\n")
        with pytest.raises(InspectionError, match="Syntax error"):
            inspect_file(f)


class TestInspectGraphEntry:
    def test_resolves_relative_path(self, tmp_path):
        sub = tmp_path / "my_agent"
        sub.mkdir()
        _write(sub, "agent.py", _SIMPLE_AGENT)
        r = inspect_graph_entry(tmp_path, "my_agent/agent.py")
        assert r.state_class == "AgentState"

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(InspectionError, match="not found"):
            inspect_graph_entry(tmp_path, "missing/agent.py")


# ── generator ─────────────────────────────────────────────────────────────────

class TestValidatePluginId:
    def test_valid_ids(self):
        for valid in ("my_agent", "ab", "agent01", "a" * 32):
            validate_plugin_id(valid)  # should not raise

    def test_too_short(self):
        with pytest.raises(ValueError):
            validate_plugin_id("a")  # only 1 char — min is 2

    def test_uppercase(self):
        with pytest.raises(ValueError):
            validate_plugin_id("MyAgent")

    def test_starts_with_digit(self):
        with pytest.raises(ValueError):
            validate_plugin_id("1agent")

    def test_hyphens_not_allowed(self):
        with pytest.raises(ValueError):
            validate_plugin_id("my-agent")

    def test_spaces_not_allowed(self):
        with pytest.raises(ValueError):
            validate_plugin_id("my agent")


class TestRender:
    def _build_config(self) -> GeneratorConfig:
        source = """\
from typing import TypedDict, List
class State(TypedDict):
    messages: List[str]
graph = None
"""
        with pytest.MonkeyPatch().context() as mp:
            pass  # just for context manager style

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as fh:
            fh.write(textwrap.dedent(source))
            tmp_name = fh.name

        result = inspect_file(Path(tmp_name))
        os.unlink(tmp_name)

        return config_from_inspection(
            inspection=result,
            plugin_id="test_agent",
            external_module="my_agent.agent",
            graph_export="graph",
            readme_excerpt="A test agent.",
        )

    def test_renders_three_files(self):
        config = self._build_config()
        files = render(config)
        assert set(files.keys()) == {"plugin.py", "graph.py", "__init__.py"}

    def test_plugin_py_is_valid_python(self):
        config = self._build_config()
        files = render(config)
        ast.parse(files["plugin.py"])  # raises SyntaxError if invalid

    def test_graph_py_is_valid_python(self):
        config = self._build_config()
        files = render(config)
        ast.parse(files["graph.py"])

    def test_plugin_id_in_output(self):
        config = self._build_config()
        files = render(config)
        assert "test_agent" in files["plugin.py"]
        assert "test_agent" in files["graph.py"]

    def test_external_module_in_graph(self):
        config = self._build_config()
        files = render(config)
        assert "my_agent.agent" in files["graph.py"]

    def test_screens_in_graph(self):
        config = self._build_config()
        files = render(config)
        # Default screens contain "welcome" key
        assert "welcome" in files["graph.py"]

    def test_invalid_plugin_id_raises(self):
        config = self._build_config()
        config.plugin_id = "INVALID"
        with pytest.raises(ValueError):
            render(config)

    def test_config_from_inspection_defaults(self):
        source = """\
from typing import TypedDict
class State(TypedDict):
    query: str
    result: str
graph = None
"""
        with pytest.MonkeyPatch().context() as mp:
            pass

        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as fh:
            fh.write(textwrap.dedent(source))
            tmp_name = fh.name

        result = inspect_file(Path(tmp_name))
        os.unlink(tmp_name)

        config = config_from_inspection(
            inspection=result,
            plugin_id="qa_agent",
            external_module="qa.agent",
            graph_export="app",
        )
        assert config.plugin_id == "qa_agent"
        assert config.input_field == "query"
        assert config.plugin_class_name == "QaAgentPlugin"
        # state fields minus skipped ones should be in initial_domain_state
        assert "query" in config.initial_domain_state or "result" in config.initial_domain_state

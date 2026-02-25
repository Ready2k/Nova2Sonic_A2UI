"""
inspector.py — Safe AST-based inspection of a LangGraph agent source file.

No code is executed — everything is done via Python's ast module.

Extracts:
  - State TypedDict class name and field definitions
  - Graph node names and the functions they map to
  - The variable name of the compiled graph (builder.compile() assignment)
  - A heuristic guess for the user-input field and agent-output field
  - A short source snippet for use as LLM context
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StateField:
    name: str
    annotation: str          # stringified type annotation
    has_reducer: bool = False  # True when Annotated[..., reducer_fn] is used


@dataclass
class NodeInfo:
    name: str                # string label used in add_node("name", ...)
    function: str            # the function/callable passed to add_node


@dataclass
class InspectionResult:
    # State
    state_class: Optional[str]
    state_fields: List[StateField]

    # Graph structure
    nodes: List[NodeInfo]
    compiled_export: Optional[str]   # variable assigned to builder.compile()

    # Heuristics
    detected_input_field: str        # best guess: "messages", "query", "input" …
    detected_output_field: str       # best guess: "messages[-1].content", "output" …

    # Extras
    pydantic_models: List[str]       # class names that inherit from BaseModel
    source_snippet: str              # first 3000 chars for LLM prompts
    warnings: List[str] = field(default_factory=list)


class InspectionError(ValueError):
    """Raised when the source file cannot be parsed."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _annotation_to_str(node: ast.expr) -> str:
    """Render an AST annotation node back to a readable string."""
    return ast.unparse(node)


def _is_typeddict(bases: list[ast.expr]) -> bool:
    for base in bases:
        if isinstance(base, ast.Name) and base.id == "TypedDict":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "TypedDict":
            return True
    return False


def _is_basemodel(bases: list[ast.expr]) -> bool:
    for base in bases:
        if isinstance(base, ast.Name) and base.id == "BaseModel":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "BaseModel":
            return True
    return False


def _extract_string_arg(node: ast.expr) -> Optional[str]:
    """Return a string literal value from an AST node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_name_arg(node: ast.expr) -> Optional[str]:
    """Return an ast.Name id or ast.Attribute attr from a node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ---------------------------------------------------------------------------
# Heuristics: detect likely input / output fields
# ---------------------------------------------------------------------------

_KNOWN_INPUT_FIELDS = ["messages", "query", "input", "user_input", "question", "text"]
_KNOWN_OUTPUT_FIELDS = {
    "messages": "messages[-1].content",
    "response": "response",
    "output": "output",
    "answer": "answer",
    "result": "result",
}


def _guess_io_fields(
    state_fields: List[StateField],
) -> tuple[str, str]:
    field_names = {f.name for f in state_fields}

    input_field = "messages"  # safe default
    for candidate in _KNOWN_INPUT_FIELDS:
        if candidate in field_names:
            input_field = candidate
            break

    output_field = _KNOWN_OUTPUT_FIELDS.get(input_field, f"{input_field}[-1]")
    for f_name, accessor in _KNOWN_OUTPUT_FIELDS.items():
        if f_name in field_names:
            output_field = accessor
            break

    return input_field, output_field


# ---------------------------------------------------------------------------
# Main inspection function
# ---------------------------------------------------------------------------

def inspect_file(source_path: Path) -> InspectionResult:
    """
    Parse source_path with ast and return an InspectionResult.

    Raises InspectionError if the file cannot be read or parsed.
    """
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InspectionError(f"Cannot read {source_path}: {exc}") from exc

    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        raise InspectionError(f"Syntax error in {source_path}: {exc}") from exc

    state_class: Optional[str] = None
    state_fields: List[StateField] = []
    pydantic_models: List[str] = []
    nodes: List[NodeInfo] = []
    compiled_export: Optional[str] = None
    warnings: List[str] = []

    # ── Walk the AST ─────────────────────────────────────────────────────────

    for node in ast.walk(tree):

        # 1. Class definitions — find TypedDicts and Pydantic models
        if isinstance(node, ast.ClassDef):
            if _is_typeddict(node.bases):
                if state_class is None:
                    # Take the first TypedDict found as the state class
                    state_class = node.name
                    for item in node.body:
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            ann_str = _annotation_to_str(item.annotation)
                            has_reducer = "Annotated" in ann_str
                            state_fields.append(StateField(
                                name=item.target.id,
                                annotation=ann_str,
                                has_reducer=has_reducer,
                            ))
                else:
                    warnings.append(
                        f"Multiple TypedDict classes found; using '{state_class}'. "
                        f"Ignoring '{node.name}'."
                    )

            elif _is_basemodel(node.bases):
                pydantic_models.append(node.name)

        # 2. Calls — find add_node and compile()
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            _check_add_node(call, nodes)

        elif isinstance(node, ast.Assign):
            # Look for: varname = builder.compile() or varname = workflow.compile()
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)
            ):
                call = node.value
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr == "compile"
                ):
                    compiled_export = node.targets[0].id

            # Also scan for add_node in chained assignments
            if isinstance(node.value, ast.Call):
                _check_add_node(node.value, nodes)

    # ── Heuristics ───────────────────────────────────────────────────────────

    if state_class is None:
        warnings.append("No TypedDict state class found — defaulting to messages-based input.")

    input_field, output_field = _guess_io_fields(state_fields)

    if compiled_export is None:
        warnings.append(
            "Could not detect compiled graph variable. "
            "You may need to set 'export_name' manually."
        )

    return InspectionResult(
        state_class=state_class,
        state_fields=state_fields,
        nodes=nodes,
        compiled_export=compiled_export,
        detected_input_field=input_field,
        detected_output_field=output_field,
        pydantic_models=pydantic_models,
        source_snippet=source[:3000],
        warnings=warnings,
    )


def _check_add_node(call: ast.Call, nodes: List[NodeInfo]) -> None:
    """
    Detect builder.add_node("name", fn) or workflow.add_node("name", fn) calls
    and append to nodes list.
    """
    if not (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_node"
    ):
        return

    args = call.args
    if len(args) < 1:
        return

    # add_node("name") — single-arg form used when function == node name
    name = _extract_string_arg(args[0])
    if name is None:
        return  # not a string literal — skip

    if len(args) >= 2:
        fn = _extract_name_arg(args[1]) or ast.unparse(args[1])
    else:
        fn = name  # LangGraph allows add_node(fn) where fn.__name__ is the node

    nodes.append(NodeInfo(name=name, function=fn))


# ---------------------------------------------------------------------------
# Utility: inspect from a repo root + graph entry
# ---------------------------------------------------------------------------

def inspect_graph_entry(
    repo_root: Path,
    file_path: str,
) -> InspectionResult:
    """
    Convenience wrapper: resolve file_path relative to repo_root and inspect it.
    """
    full_path = repo_root / file_path
    if not full_path.exists():
        raise InspectionError(
            f"Graph source file not found: {full_path}"
        )
    return inspect_file(full_path)

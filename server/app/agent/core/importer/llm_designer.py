"""
llm_designer.py — LLM-assisted A2UI component tree design for imported agents.

Two-pass pipeline
─────────────────
Pass 1  (Haiku)   Fast field-mapping: confirm which state key is the user input
                  and how to access the agent's response.  Only runs when the
                  inspector's heuristics are uncertain (i.e. no recognised field).

Pass 2  (Sonnet)  Full A2UI design: generates welcome / result / error screens,
                  voice text, initial domain state, and reasoning notes.

Fallback chain
──────────────
  Sonnet LLM call fails → retry with Haiku model
  Haiku LLM call fails  → use Phase-1 minimal DataCard defaults (no exception raised)
  Bedrock not configured → same minimal defaults

All LLM calls are wrapped in asyncio.to_thread so they don't block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.agent.core.importer.inspector import InspectionResult

logger = logging.getLogger(__name__)

# ── Model IDs ─────────────────────────────────────────────────────────────────

_DEFAULT_SONNET = os.getenv(
    "DESIGNER_SONNET_MODEL_ID",
    "us.anthropic.claude-sonnet-4-6-20251101-v1:0",
)
_DEFAULT_HAIKU = os.getenv(
    "DESIGNER_HAIKU_MODEL_ID",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)

_AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Fields that the inspector reliably detects without LLM help
_CONFIDENT_INPUT_FIELDS = {"messages", "query", "input", "user_input", "question", "text"}


# ── Available A2UI components (for the system prompt) ─────────────────────────

_A2UI_COMPONENT_GUIDE = """
AVAILABLE A2UI COMPONENTS
─────────────────────────
Layout
  Column   { id, component:"Column", children:[id,...] }
  Row      { id, component:"Row",    children:[id,...] }

Content
  Text       { id, component:"Text",     text:"...", data:{variant:"h1"|"h2"|"h3"|"body"} }
  DataCard   { id, component:"DataCard", text:"Card title", data:{status?:"...", detail:"..."} }
  BenefitCard{ id, component:"BenefitCard", text:"Title", variant:"Info"|"Warning"|"Success",
               data:{detail:"..."} }
  Image      { id, component:"Image",    data:{src:"...", alt:"..."} }

Data
  Gauge         { id, component:"Gauge",         data:{value:0-100, label:"..."} }
  StatCard      { id, component:"StatCard",      text:"Label", data:{value:"...", unit:"..."} }
  ProgressBar   { id, component:"ProgressBar",   data:{value:0-100, label:"..."} }
  ComparisonBadge { id, component:"ComparisonBadge", data:{label:"...", value:"...", better:bool} }
  ProductCard   { id, component:"ProductCard",   data:{name:"...", rate:"...", monthly_payment:"...",
                  features:[]} }
  Timeline      { id, component:"Timeline",      data:{steps:["Step1",...], current:0} }

Action
  Button   { id, component:"Button", text:"Label", data:{action:"<plugin_id>.<action_name>"} }

Special
  Map   { id, component:"Map", data:{lat:51.5, lng:-0.1, zoom:13} }

RULES
─────
• Root component id must always be "root" with component "Column".
• All ids referenced in children must exist in the same components array.
• Button action strings must be namespaced: "<plugin_id>.<verb>_<noun>"
• Voice text ≤ 40 words, warm British banking tone.
• Use DataCard as the default single-content component.
• Use Timeline when the agent has a clear multi-step journey.
• Use BenefitCard variant:"Warning" for alerts or errors.
• Do NOT invent component types not listed above.
"""


# ── Pydantic models for LLM response validation ───────────────────────────────

class ScreenDef(BaseModel):
    title: str = Field(description="Page/modal title shown in the header")
    voice_text: str = Field(description="Text read aloud by TTS; max 40 words")
    components: List[Dict[str, Any]] = Field(
        description="Flat list of A2UI component objects; must contain a root Column"
    )


class FieldMapping(BaseModel):
    input_field: str = Field(
        description="State key that holds the user's question, e.g. 'messages' or 'query'"
    )
    output_accessor: str = Field(
        description=(
            "Python expression to extract agent response from ext_result, "
            "e.g. 'messages[-1].content' or 'output'"
        )
    )


class A2UIDesign(BaseModel):
    screens: Dict[str, ScreenDef] = Field(
        description=(
            "A2UI screens keyed by name. Must include 'welcome', 'result', 'error'. "
            "Additional screens (e.g. 'collecting', 'confirmation') are allowed."
        )
    )
    input_field: str = Field(
        description="State key receiving user input, e.g. 'messages'"
    )
    output_accessor: str = Field(
        description="Python expression to get agent response, e.g. 'messages[-1].content'"
    )
    initial_domain_state: Dict[str, Any] = Field(
        default_factory=dict,
        description="Default values for domain[plugin_id] — one entry per tracked field",
    )
    reasoning: str = Field(
        description="1-3 sentences explaining the design choices"
    )


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class DesignResult:
    screens: Dict[str, dict]          # ready for generator.py
    input_field: str
    output_accessor: str
    initial_domain_state: Dict[str, Any]
    reasoning: str
    used_fallback: bool = False
    fallback_reason: Optional[str] = None


# ── Component validation ──────────────────────────────────────────────────────

_KNOWN_COMPONENTS = {
    "Column", "Row", "Text", "DataCard", "BenefitCard", "Image",
    "Gauge", "StatCard", "ProgressBar", "ComparisonBadge", "ProductCard",
    "Timeline", "Button", "Map",
}


def _validate_and_fix_components(
    components: List[Dict[str, Any]],
    plugin_id: str,
    screen_key: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Validate a component list and apply light auto-fixes.
    Returns (fixed_components, warning_list).
    """
    warnings: List[str] = []
    ids = {c.get("id") for c in components if isinstance(c, dict)}

    # Ensure root exists
    has_root = any(c.get("id") == "root" for c in components if isinstance(c, dict))
    if not has_root:
        warnings.append(f"[{screen_key}] Missing root component — adding one.")
        child_ids = [c.get("id") for c in components if isinstance(c, dict) and c.get("id")]
        components = [
            {"id": "root", "component": "Column", "children": child_ids},
            *components,
        ]
        ids.add("root")

    fixed = []
    for comp in components:
        if not isinstance(comp, dict):
            warnings.append(f"[{screen_key}] Skipping non-dict component: {comp!r}")
            continue

        comp_type = comp.get("component", "")
        if comp_type not in _KNOWN_COMPONENTS:
            warnings.append(
                f"[{screen_key}] Unknown component '{comp_type}' — replacing with DataCard."
            )
            comp = {
                "id": comp.get("id", "fallback"),
                "component": "DataCard",
                "text": comp.get("text", "Content"),
                "data": {"detail": str(comp.get("data", ""))},
            }

        # Check children references
        children = comp.get("children", [])
        valid_children = [c for c in children if c in ids]
        missing = set(children) - ids
        if missing:
            warnings.append(
                f"[{screen_key}] Component '{comp.get('id')}' references unknown children: "
                f"{missing} — removed."
            )
            comp = {**comp, "children": valid_children}

        fixed.append(comp)

    return fixed, warnings


def _validate_screen(screen: ScreenDef, plugin_id: str, key: str) -> Tuple[dict, List[str]]:
    """Convert a ScreenDef to a plain dict, validating components."""
    components, warnings = _validate_and_fix_components(
        screen.components, plugin_id, key
    )
    return {
        "title": screen.title,
        "voice_text": screen.voice_text[:300],
        "components": components,
    }, warnings


# ── Phase-1 fallback screens ──────────────────────────────────────────────────

def _fallback_screens(plugin_id: str, agent_name: str) -> Dict[str, dict]:
    return {
        "welcome": {
            "title": agent_name,
            "voice_text": f"Hello! I'm your {agent_name} assistant. How can I help?",
            "components": [
                {"id": "root", "component": "Column", "children": ["welcome_card"]},
                {
                    "id": "welcome_card", "component": "DataCard", "text": agent_name,
                    "data": {"detail": "How can I help you today? Type or speak your question."},
                },
            ],
        },
        "result": {
            "title": "Response",
            "voice_text": "{response}",
            "components": [
                {"id": "root", "component": "Column", "children": ["result_card"]},
                {
                    "id": "result_card", "component": "DataCard", "text": "Response",
                    "data": {"detail": "{response}"},
                },
            ],
        },
        "error": {
            "title": "Something went wrong",
            "voice_text": "Sorry, something went wrong. Please try again.",
            "components": [
                {"id": "root", "component": "Column", "children": ["error_card"]},
                {
                    "id": "error_card", "component": "BenefitCard", "text": "Error",
                    "variant": "Warning",
                    "data": {"detail": "Something went wrong. Please try again."},
                },
            ],
        },
    }


# ── Prompt builders ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = f"""You are an expert in the A2UI voice-first banking assistant component system.

Your task: given a LangGraph agent's schema and purpose, design the A2UI screens that will
display its output in a Barclays digital banking context.

{_A2UI_COMPONENT_GUIDE}

You MUST respond with valid JSON matching the schema exactly. No markdown, no explanation text
outside the JSON structure.
"""


def _build_design_prompt(
    plugin_id: str,
    inspection: InspectionResult,
    readme_excerpt: str,
    hinted_input_field: str,
    hinted_output_accessor: str,
) -> str:
    state_fields_text = "\n".join(
        f"  {f['name']}: {f['annotation']}"
        for f in [{"name": sf.name, "annotation": sf.annotation} for sf in inspection.state_fields]
    ) or "  (none detected)"

    nodes_text = "\n".join(
        f"  {n.name}  →  {n.function}()"
        for n in inspection.nodes
    ) or "  (none detected)"

    pydantic_text = (
        "  " + ", ".join(inspection.pydantic_models)
        if inspection.pydantic_models else "  (none)"
    )

    return f"""Plugin ID : {plugin_id}
Agent name: {" ".join(w.capitalize() for w in plugin_id.split("_"))}

README excerpt (agent purpose):
{readme_excerpt or "(not available)"}

State schema ({inspection.state_class or "TypedDict"}):
{state_fields_text}

Graph nodes:
{nodes_text}

Pydantic output models:
{pydantic_text}

Inspector heuristics (use as a starting point, override if needed):
  input_field    = "{hinted_input_field}"
  output_accessor= "{hinted_output_accessor}"

Source snippet:
{inspection.source_snippet[:800]}

Task:
1. Design A2UI screens appropriate for this agent's purpose.
   Required: "welcome", "result", "error".
   Optional: add named screens like "collecting", "confirmation", "processing" if the
   agent has a multi-step journey.
2. For "result", use {{response}} as a placeholder in voice_text and DataCard detail —
   it will be substituted at runtime with the agent's actual output.
3. Set input_field and output_accessor to the correct values for this agent.
4. Propose initial_domain_state fields that represent any agent-specific state worth
   tracking (can be empty dict {{}} for simple text-in/text-out agents).
5. Explain your design in 2-3 sentences in the "reasoning" field.

Respond with JSON only — no markdown fences.
"""


_FIELD_MAPPING_PROMPT = """Given this LangGraph agent state schema, identify the user-input field and output accessor.

State class: {state_class}
Fields:
{fields}

Source snippet:
{snippet}

Respond with JSON only:
{{"input_field": "...", "output_accessor": "..."}}
"""


# ── LLM call helpers ──────────────────────────────────────────────────────────

def _invoke_converse(model_id: str, system: str, user: str) -> str:
    """
    Synchronous Bedrock Converse API call.
    Returns the assistant's text response.
    Raises on any Bedrock / network error.
    """
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(
        model=model_id,
        region_name=_AWS_REGION,
        max_tokens=4096,
        temperature=0,   # deterministic output for structured generation
    )
    from langchain_core.messages import HumanMessage, SystemMessage
    response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return response.content if hasattr(response, "content") else str(response)


def _invoke_structured(model_id: str, system: str, user: str, schema) -> Any:
    """
    Invoke Bedrock with structured output via tool calling.
    Returns a validated Pydantic model instance.
    Raises ValidationError or any Bedrock error.
    """
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(
        model=model_id,
        region_name=_AWS_REGION,
        max_tokens=4096,
        temperature=0,
    )
    structured_llm = llm.with_structured_output(schema)
    from langchain_core.messages import HumanMessage, SystemMessage
    return structured_llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])


# ── Pass 1: Field mapping (Haiku) ─────────────────────────────────────────────

def _map_fields_sync(inspection: InspectionResult) -> Tuple[str, str]:
    """
    Use Haiku to confirm input/output field mapping.
    Returns (input_field, output_accessor).
    Falls back to inspector heuristics on any failure.
    """
    # Skip LLM call if inspector is already confident
    if inspection.detected_input_field in _CONFIDENT_INPUT_FIELDS:
        logger.debug("[LLMDesigner] Field mapping: using inspector heuristic (%s)",
                     inspection.detected_input_field)
        return inspection.detected_input_field, inspection.detected_output_field

    fields_text = "\n".join(
        f"  {sf.name}: {sf.annotation}" for sf in inspection.state_fields
    ) or "  (none)"

    prompt = _FIELD_MAPPING_PROMPT.format(
        state_class=inspection.state_class or "AgentState",
        fields=fields_text,
        snippet=inspection.source_snippet[:600],
    )
    system = "You identify LangGraph state field mappings. Respond with JSON only."

    try:
        raw = _invoke_converse(_DEFAULT_HAIKU, system, prompt)
        # Strip any accidental markdown fences
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(raw)
        mapping = FieldMapping.model_validate(data)
        logger.info("[LLMDesigner] Haiku field mapping: %s → %s",
                    mapping.input_field, mapping.output_accessor)
        return mapping.input_field, mapping.output_accessor
    except Exception as exc:
        logger.warning("[LLMDesigner] Haiku field mapping failed (%s) — using heuristics", exc)
        return inspection.detected_input_field, inspection.detected_output_field


# ── Pass 2: Full A2UI design (Sonnet → Haiku fallback) ───────────────────────

def _design_sync(
    plugin_id: str,
    inspection: InspectionResult,
    readme_excerpt: str,
    input_field: str,
    output_accessor: str,
    model_id: str,
) -> A2UIDesign:
    """
    Call the given model to produce an A2UIDesign.
    Raises on failure (caller handles fallback).
    """
    user_prompt = _build_design_prompt(
        plugin_id=plugin_id,
        inspection=inspection,
        readme_excerpt=readme_excerpt,
        hinted_input_field=input_field,
        hinted_output_accessor=output_accessor,
    )
    return _invoke_structured(model_id, _SYSTEM_PROMPT, user_prompt, A2UIDesign)


# ── Refine prompt ─────────────────────────────────────────────────────────────

_REFINE_SYSTEM_PROMPT = f"""You are an expert in the A2UI voice-first banking assistant component system.

Your task is to update existing A2UI screen definitions based on a user's request.

{_A2UI_COMPONENT_GUIDE}

You MUST respond with valid JSON in this exact format:
{{"screens": {{<screen_key>: <screen_def>, ...}}, "reasoning": "1-2 sentences explaining changes"}}

Rules:
- Return ALL screens (include unchanged ones).
- Preserve the 'result' screen's {{response}} placeholders.
- No markdown fences, no explanation text outside the JSON.
"""


def _refine_sync(
    plugin_id: str,
    current_screens: Dict[str, Any],
    user_request: str,
    readme_excerpt: str,
    model_id: str,
) -> DesignResult:
    agent_name = " ".join(w.capitalize() for w in plugin_id.split("_"))
    user_prompt = (
        f"Plugin ID: {plugin_id}\n"
        f"Agent description: {readme_excerpt or '(not available)'}\n\n"
        f"Current screens:\n{json.dumps(current_screens, indent=2)}\n\n"
        f"User request: {user_request}\n\n"
        "Update the screens to satisfy the request. Return all screens (including unchanged ones)."
    )

    raw = _invoke_converse(model_id, _REFINE_SYSTEM_PROMPT, user_prompt)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    data = json.loads(raw)

    screens_raw = data.get("screens", current_screens)
    reasoning = data.get("reasoning", "Screens updated.")

    all_warnings: List[str] = []
    screens_out: Dict[str, dict] = {}
    for key, screen_raw in screens_raw.items():
        if isinstance(screen_raw, dict):
            screen_def = ScreenDef.model_validate(screen_raw)
            screen_dict, comp_warnings = _validate_screen(screen_def, plugin_id, key)
            screens_out[key] = screen_dict
            all_warnings.extend(comp_warnings)

    # Ensure mandatory screens are present (fall back to current or defaults)
    for required_key in ("welcome", "result", "error"):
        if required_key not in screens_out:
            screens_out[required_key] = current_screens.get(
                required_key, _fallback_screens(plugin_id, agent_name)[required_key]
            )

    if all_warnings:
        logger.warning("[LLMDesigner] Refine component warnings: %s", all_warnings)

    return DesignResult(
        screens=screens_out,
        input_field="messages",
        output_accessor="messages[-1].content",
        initial_domain_state={},
        reasoning=reasoning,
        used_fallback=False,
    )


async def refine(
    plugin_id: str,
    current_screens: Dict[str, Any],
    user_request: str,
    readme_excerpt: str = "",
    sonnet_model_id: str = _DEFAULT_SONNET,
    haiku_model_id: str = _DEFAULT_HAIKU,
) -> DesignResult:
    """
    Refine existing A2UI screens based on a conversational user request.

    Fallback chain: Sonnet → Haiku → unchanged screens (never raises).
    """
    for model_label, model_id in [("Sonnet", sonnet_model_id), ("Haiku", haiku_model_id)]:
        try:
            logger.info("[LLMDesigner] Refine with %s (%s)", model_label, model_id)
            result = await asyncio.to_thread(
                _refine_sync, plugin_id, current_screens, user_request, readme_excerpt, model_id
            )
            logger.info("[LLMDesigner] Refine complete (%s). Screens: %s",
                        model_label, list(result.screens.keys()))
            return result
        except Exception as exc:
            logger.warning("[LLMDesigner] Refine %s failed: %s — %s", model_label, type(exc).__name__, exc)

    logger.warning("[LLMDesigner] All refine attempts failed — returning unchanged screens")
    return DesignResult(
        screens=current_screens,
        input_field="messages",
        output_accessor="messages[-1].content",
        initial_domain_state={},
        reasoning="Unable to process the request right now — screens unchanged.",
        used_fallback=True,
        fallback_reason="All LLM attempts failed during refine",
    )


# ── Public async entry point ──────────────────────────────────────────────────

async def design(
    plugin_id: str,
    inspection: InspectionResult,
    readme_excerpt: str = "",
    sonnet_model_id: str = _DEFAULT_SONNET,
    haiku_model_id: str = _DEFAULT_HAIKU,
) -> DesignResult:
    """
    Produce an A2UI design for an imported agent.

    Fallback chain:
      Sonnet → Haiku → Phase-1 minimal defaults

    Never raises; always returns a usable DesignResult.
    """
    agent_name = " ".join(w.capitalize() for w in plugin_id.split("_"))
    all_warnings: List[str] = []

    # ── Pass 1: Field mapping ─────────────────────────────────────────────────
    try:
        input_field, output_accessor = await asyncio.to_thread(
            _map_fields_sync, inspection
        )
    except Exception as exc:
        logger.warning("[LLMDesigner] Field mapping thread error: %s", exc)
        input_field = inspection.detected_input_field
        output_accessor = inspection.detected_output_field

    # ── Pass 2: Design (Sonnet) ───────────────────────────────────────────────
    design_result: Optional[A2UIDesign] = None
    fallback_reason: Optional[str] = None

    for model_label, model_id in [("Sonnet", sonnet_model_id), ("Haiku", haiku_model_id)]:
        try:
            logger.info("[LLMDesigner] Calling %s (%s) for A2UI design", model_label, model_id)
            design_result = await asyncio.to_thread(
                _design_sync, plugin_id, inspection, readme_excerpt,
                input_field, output_accessor, model_id,
            )
            logger.info("[LLMDesigner] %s design complete. Screens: %s",
                        model_label, list(design_result.screens.keys()))
            break
        except Exception as exc:
            fallback_reason = f"{model_label} failed: {exc}"
            logger.warning("[LLMDesigner] %s design failed: %s — %s",
                           model_label, type(exc).__name__, exc)
            if model_label == "Haiku":
                logger.warning("[LLMDesigner] All LLM attempts failed — using Phase-1 defaults")

    # ── Fallback to Phase-1 defaults ─────────────────────────────────────────
    if design_result is None:
        return DesignResult(
            screens=_fallback_screens(plugin_id, agent_name),
            input_field=input_field,
            output_accessor=output_accessor,
            initial_domain_state={},
            reasoning="LLM design unavailable — using Phase-1 minimal DataCard defaults.",
            used_fallback=True,
            fallback_reason=fallback_reason,
        )

    # ── Validate and fix components ───────────────────────────────────────────
    screens_out: Dict[str, dict] = {}
    for key, screen_def in design_result.screens.items():
        screen_dict, comp_warnings = _validate_screen(screen_def, plugin_id, key)
        screens_out[key] = screen_dict
        all_warnings.extend(comp_warnings)

    # Ensure mandatory screens exist
    for required_key in ("welcome", "result", "error"):
        if required_key not in screens_out:
            all_warnings.append(
                f"Screen '{required_key}' missing from LLM response — using fallback."
            )
            screens_out[required_key] = _fallback_screens(plugin_id, agent_name)[required_key]

    if all_warnings:
        logger.warning("[LLMDesigner] Component warnings: %s", all_warnings)

    return DesignResult(
        screens=screens_out,
        input_field=design_result.input_field,
        output_accessor=design_result.output_accessor,
        initial_domain_state=design_result.initial_domain_state,
        reasoning=design_result.reasoning,
        used_fallback=False,
    )

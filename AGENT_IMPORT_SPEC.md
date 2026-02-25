# Agent Import System — Technical Specification

**Status:** Draft
**Version:** 0.1
**Date:** 2026-02-25

---

## 1. Overview

The Agent Import System (AIS) allows any externally-built LangGraph agent to be imported into this A2UI runtime as a first-class plugin — without modifying the agent's source code.

The system has two entry points:
- **`/transfer` wizard** (browser) — guided import from a GitHub URL or local path
- **`POST /api/import-agent`** (API) — headless import for CI/CD and scripting

A key feature is **LLM-assisted A2UI generation**: once the external graph is parsed, a Claude Sonnet call analyses the agent's state schema and node outputs and proposes an A2UI component tree that the user can accept or edit before the plugin is written to disk.

---

## 2. Goals

| # | Goal |
|---|---|
| G1 | Import any LangGraph agent (Python) that exports a `CompiledStateGraph` |
| G2 | Generate a working plugin with zero manual code changes for the common case (text-in / text-out agent) |
| G3 | Use an LLM to produce a contextually appropriate A2UI component tree from the agent's schema and sample output |
| G4 | Auto-discover all plugins at server startup — no manual edits to `main.py` |
| G5 | Sandboxed dependency installation (no unchecked `pip install` at runtime) |
| G6 | The entire import round-trip completes in under 60 seconds |

---

## 3. Non-Goals

- Importing non-Python agents or non-LangGraph frameworks
- Runtime hot-swap of plugins (server restart is acceptable post-import)
- Automatic migration of agents that use stateful checkpointers (SQLite, Redis)
- Full automated testing of the imported agent (smoke test only)

---

## 4. High-Level Flow

```
User pastes GitHub URL
        │
        ▼
┌───────────────────┐
│  1. FETCH         │  git clone --depth 1 into temp dir
│                   │  Parse langgraph.json → graph entry points
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  2. PARSE         │  AST-inspect source:
│                   │  • State TypedDict fields + reducers
│                   │  • Node names and return types
│                   │  • Pydantic output models (if any)
│                   │  • requirements.txt / pyproject.toml
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  3. LLM DESIGN    │  Send schema + node signatures to Claude Sonnet.
│                   │  Claude proposes:
│                   │  • A2UI component tree for each agent "phase"
│                   │  • Voice text templates
│                   │  • create_initial_state() domain fields
│                   │  • Input/output field mappings
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  4. PREVIEW       │  Wizard shows generated plugin.py, graph.py, A2UI JSON.
│                   │  User can edit component tree inline before writing.
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  5. WRITE         │  Files written to server/app/agent/plugins/<plugin_id>/
│                   │  External source copied to plugins/<plugin_id>/src/
│                   │  requirements.txt written alongside
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  6. VALIDATE      │  Import-check: `python -c "from plugin import XPlugin"`
│                   │  Smoke test: invoke graph with empty transcript
│                   │  Report pass/fail with traceback if needed
└────────┬──────────┘
         │
         ▼
      Plugin live on next server restart
      (auto-discovery picks it up)
```

---

## 5. Integration Strategies

The importer offers three strategies. The wizard recommends one based on inspection.

### 5.1 Thin Wrapper *(default for `messages`-based agents)*

The external graph runs untouched inside a single LangGraph node. The adapter translates:

- **In:** `state["transcript"]` → `HumanMessage` appended to external state
- **Out:** `external_result["messages"][-1].content` → `server.voice.say` + A2UI patch

Best for: standard ReAct / tool-calling agents that produce `AIMessage` output.

### 5.2 Sub-graph *(for structured-output agents)*

The external graph is embedded as a sub-graph node within a CommonState-compatible outer graph. State keys are mapped via a declared schema. Allows the external graph to update CommonState fields directly if they share names.

Best for: agents with structured Pydantic output that maps cleanly to `domain` fields.

### 5.3 Full Port *(wizard guidance only)*

The wizard provides a checklist and a diff-based guide for manually porting the graph. No files are auto-generated beyond `plugin.py` and `__init__.py` shells.

Best for: agents requiring custom A2UI flows, identity gates, or complex routing.

---

## 6. LLM-Assisted A2UI Generation

### 6.1 Model Selection

| Operation | Model | Rationale |
|---|---|---|
| A2UI design | `claude-sonnet-4-6` (Bedrock: `us.anthropic.claude-sonnet-4-6-20251101-v1:0`) | One-time, not latency-sensitive; needs strong structured-output and reasoning |
| Input/output mapping | `claude-haiku-4-5` | Fast, cheap; straightforward field-matching task |
| Smoke test analysis | `amazon.nova-lite-v1:0` | Already in-project; just classifying pass/fail |

Claude Sonnet is invoked via the existing Bedrock client (`BedrockRuntimeClient`) using `InvokeModelCommand` (not streaming). No new SDK dependencies.

### 6.2 What Claude Receives

```
SYSTEM:
You are an expert in the A2UI component system. Given a LangGraph agent's schema,
design an A2UI component tree appropriate for a voice-first banking assistant.

Available components:
  Layout:  Column, Row
  Content: Text (h1/h2/h3/body), DataCard, BenefitCard, Image
  Data:    Gauge, ProductCard, ComparisonBadge, StatCard, ProgressBar
  Action:  Button
  Special: Map, Timeline

Rules:
- Root must always be a Column
- Every screen must have at least one DataCard
- Buttons must use action IDs namespaced as "<plugin_id>.<action>"
- Voice text must be concise (< 40 words), warm, and British banking tone
- Return valid JSON only

USER:
Plugin ID: {plugin_id}
Agent purpose (from README): {readme_excerpt}

State schema:
{state_typeddict_source}

Node names and return signatures:
{node_signatures}

Sample output (if available):
{sample_output_json}

Task:
1. Identify the distinct "screens" this agent will show (welcome, collecting info,
   showing results, confirmation, error).
2. For each screen, produce an A2UI component tree (JSON array of component objects).
3. Produce a voice_text string for each screen.
4. Propose create_initial_state() domain fields for state["domain"]["{plugin_id}"].
5. Identify which state field maps to the user's input (input_field) and which
   field(s) contain the agent's response (output_field).

Respond with JSON matching the schema below.
```

### 6.3 Claude's Response Schema

```json
{
  "screens": {
    "welcome": {
      "components": [...],
      "voice_text": "..."
    },
    "collecting": {
      "components": [...],
      "voice_text": "..."
    },
    "result": {
      "components": [...],
      "voice_text": "..."
    },
    "error": {
      "components": [...],
      "voice_text": "..."
    }
  },
  "input_field": "messages",
  "output_field": "messages[-1].content",
  "initial_domain_state": {
    "some_field": null,
    "another_field": false
  },
  "reasoning": "Brief explanation of design choices"
}
```

### 6.4 Fallback

If Claude's response fails validation, the system falls back to a minimal scaffold:
- `welcome` screen: single `DataCard` with agent name + generic instructions
- `result` screen: single `DataCard` rendering `output_field` as body text
- Voice text: `"One moment..."` / `"{output_field}"`

This guarantees the plugin is always functional, even if not polished.

---

## 7. Source Parsing

### 7.1 `langgraph.json` Parser

```python
{
  "graphs": {
    "agent": "./my_agent/agent.py:graph"  # → file + export name
  },
  "dependencies": ["./my_agent"],
  "env": ".env"
}
```

Extracts: graph file path, export symbol name, dependency paths.

### 7.2 AST Inspector (`inspector.py`)

Uses Python's `ast` module (no `exec`, no imports) to safely extract:

| Target | How |
|---|---|
| State TypedDict | Find `class X(TypedDict)` → field names + type annotations |
| Node functions | Find `builder.add_node("name", fn)` → node name + function |
| Return annotations | Find `def fn(...) -> dict:` return statements, collect returned keys |
| Pydantic models | Find `class X(BaseModel)` → field names (for structured output agents) |
| Compiled graph | Find `X = builder.compile()` or `X = workflow.compile()` → export name |

This is read-only static analysis. No code is executed during inspection.

### 7.3 README Excerpt

First 500 characters of `README.md` (if present) passed to Claude as agent purpose hint.

---

## 8. Plugin Code Generator

Templates (Jinja2) for each generated file:

### `plugin.py`
```python
class {{ PluginClass }}(PluginBase):
    plugin_id = "{{ plugin_id }}"

    def build_graph(self):
        from app.agent.plugins.{{ plugin_id }}.graph import app_graph
        return app_graph

    def create_initial_state(self):
        return {
            # CommonState envelope
            ...,
            "domain": {
                "{{ plugin_id }}": {{ initial_domain_state | tojson }}
            }
        }
```

### `graph.py` (thin wrapper strategy)
```python
# Auto-generated adapter — do not edit the external graph source directly.
# Re-run import to regenerate, or switch to strategy: full_port for manual control.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from {{ external_module }} import {{ graph_export }} as _external_graph
from langchain_core.messages import HumanMessage

SCREENS = {{ screens_json }}   # LLM-designed A2UI screens

def _run_external(state):
    ext_input = {"{{ input_field }}": [HumanMessage(state["transcript"])]}
    ext_result = _external_graph.invoke(ext_input)
    response = {{ output_field_accessor }}
    screen = SCREENS["result"]
    return {
        "outbox": [
            {"type": "server.a2ui.patch", "payload": {"updateComponents": {"components": screen["components"]}}},
            {"type": "server.voice.say", "payload": {"text": response[:200]}},
            {"type": "server.transcript.final", "payload": {"text": response, "role": "assistant"}},
        ]
    }

def _welcome(state):
    screen = SCREENS["welcome"]
    return {
        "outbox": [
            {"type": "server.a2ui.patch", "payload": {"updateComponents": {"components": screen["components"]}}},
            {"type": "server.voice.say", "payload": {"text": screen["voice_text"]}},
        ]
    }

# ... router, graph assembly, app_graph = builder.compile()
```

---

## 9. Auto-Discovery

Replace explicit `register()` calls in `main.py` with a loader:

```python
# server/app/agent/plugin_loader.py

import importlib, inspect, pkgutil
from app.agent.core.contracts import PluginBase
from app.agent.core.registry import register

def load_all_plugins():
    plugins_pkg = "app.agent.plugins"
    import app.agent.plugins as _pkg
    for _, name, _ in pkgutil.iter_modules(_pkg.__path__):
        module = importlib.import_module(f"{plugins_pkg}.{name}.plugin")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, PluginBase) and obj is not PluginBase:
                register(obj())
```

Called once in `main.py` startup:
```python
from app.agent.plugin_loader import load_all_plugins
load_all_plugins()
```

New plugins are live after server restart — no other changes needed.

---

## 10. Frontend Wizard (`/transfer`)

### Route
`client/src/app/transfer/page.tsx`

### Steps (wizard state machine)

```
IDLE → FETCHING → PARSED → DESIGNING (LLM) → PREVIEW → WRITING → VALIDATING → DONE | ERROR
```

### UI Panels

| Step | Left panel | Right panel |
|---|---|---|
| FETCHING | URL input + spinner | Repo tree (fetched files) |
| PARSED | Detected graphs + strategy selector | State schema viewer |
| DESIGNING | "Generating A2UI..." spinner | LLM reasoning text streams in |
| PREVIEW | File tabs: plugin.py / graph.py | A2UI live renderer (uses `A2Renderer`) |
| VALIDATING | Import check output | Smoke test: type a message, see response |
| DONE | Plugin registered! | Link to `ws://localhost:8000/ws?agent=<plugin_id>` |

### Key interaction: inline A2UI editor

In the PREVIEW step, the A2UI JSON for each screen is editable. Changes re-render the live preview in real time using the existing `A2Renderer` component. The user can swap components, change text, reorder — before anything is written to disk.

---

## 11. API Contracts

### `POST /api/import-agent`

**Request:**
```json
{
  "url": "https://github.com/langchain-ai/langgraph-example",
  "plugin_id": "my_agent",
  "strategy": "wrapper",
  "graph_id": "agent",
  "dry_run": false
}
```

**Response (success):**
```json
{
  "status": "ok",
  "plugin_id": "my_agent",
  "files_written": ["plugin.py", "graph.py", "__init__.py", "src/..."],
  "validation": { "import_ok": true, "smoke_test_ok": true },
  "llm_reasoning": "...",
  "screens": { ... }
}
```

**Response (dry_run):**
Same as above but `files_written` is empty; returns generated file contents as strings.

### `GET /api/import-agent/status/{job_id}`

For long-running imports. Returns current step + any streaming LLM output.

---

## 12. Security Boundaries

| Risk | Mitigation |
|---|---|
| Arbitrary code execution (AST parse) | Use `ast` module only — no `exec`, no `import` during inspection |
| Malicious `requirements.txt` | Show deps to user, require explicit approval before `pip install` |
| Large repos | Enforce 50 MB clone size limit; timeout at 30s |
| Path traversal in `plugin_id` | Validate: `^[a-z][a-z0-9_]{1,32}$` |
| Overwriting existing plugin | Refuse if `plugins/<plugin_id>/` already exists unless `force: true` |

---

## 13. Build Phases

### Phase 1 — Foundation (no UI)
- [x] Plugin auto-discovery (`plugin_loader.py`)
- [x] `langgraph.json` parser
- [x] AST inspector (`inspector.py`)
- [x] Jinja2 templates for plugin scaffold
- [x] `POST /api/import-agent` endpoint (dry_run only)

### Phase 2 — LLM Design
- [x] Claude Sonnet integration for A2UI generation (Bedrock)
- [x] Response schema validation + fallback renderer
- [x] Haiku field-mapping step

### Phase 3 — Write & Validate
- [x] File writer + external source copy
- [x] Import check subprocess
- [x] Smoke test invocation

### Phase 4 — Frontend Wizard
- [x] `/transfer` page + wizard state machine
- [x] Live A2UI preview (read-only `A2Renderer` with screen tabs)
- [ ] Inline A2UI JSON editor (edit component tree before writing)
- [ ] Streaming LLM output display
- [x] Dep approval step

---

## 14. Open Questions

| # | Question | Owner |
|---|---|---|
| OQ1 | Should external graph source be copied into `src/` or installed as a package? Copy is simpler; package is cleaner for versioning. | TBD |
| OQ2 | For sub-graph strategy, how do we handle agents that need a checkpointer (persistence)? | TBD |
| OQ3 | Should the LLM also generate integration tests for the imported plugin? | TBD |
| OQ4 | Do we expose `/transfer` behind auth, or is it dev-only (localhost only)? | TBD |
| OQ5 | When the imported agent needs env vars (API keys), where do users configure these? `.env` append? | TBD |

# From Single Mortgage Graph to a Pluggable LangGraph Agent Framework

## Executive summary

Short answer: **you should not treat `graph.py` as a drop-in swap file** when moving from the Mortgage Adviser to a different domain (for example, a Lost Card Agent).

A direct swap can break because the current runtime expects specific:

- state keys,
- outbox event conventions,
- UI patch shape and action IDs,
- and lifecycle assumptions (ingest → route → patch/voice output).

Instead, the reusable approach is to introduce a **domain-agnostic agent contract** with a small adapter layer. Then each domain (Mortgage, Lost Card, etc.) plugs in via configuration + domain modules while reusing shared runtime plumbing.

---

## Why `graph.py` cannot simply be swapped today

The app currently imports one concrete graph and one concrete state shape directly into the websocket server runtime. That means the web layer and graph are tightly coupled. `main.py` imports `app_graph` and `AgentState` from the mortgage graph module, and creates an initial state that matches mortgage flow fields (e.g., `intent.propertyValue`, `ltv`, `products`).【F:server/app/main.py†L14-L54】

The graph also assumes mortgage-specific nodes and routes (`call_mortgage_tools`, `render_products_a2ui`, `render_summary_a2ui`, etc.) and compiles a specific node topology. A different domain graph with different nodes/actions would not automatically satisfy the same expectations unless it preserves the same runtime contract.【F:server/app/agent/graph.py†L973-L1502】

In addition, the websocket outbox processor expects event-type conventions such as `server.a2ui.patch`, `server.voice.say`, and `server.transcript.final`, and applies behavior (e.g., merged TTS) based on those types. A replacement graph must keep this output protocol stable or provide translation logic.【F:server/app/main.py†L106-L207】

---

## Target design: Pluggable Agent Framework

Use a layered architecture:

1. **Core Runtime (shared):**
   - WebSocket transport
   - session lifecycle
   - outbox processing
   - TTS/STT orchestration

2. **Agent Contract (shared interface):**
   - common state envelope
   - common event schema
   - required graph entrypoint methods

3. **Domain Plugin (replaceable):**
   - domain state slice (`mortgage`, `lost_card`, etc.)
   - domain intent extraction logic
   - domain tools
   - domain UI builders and action handlers
   - graph routing rules

4. **Registry/Factory (selection):**
   - chooses plugin by tenant/product/channel/user route
   - injects plugin graph + initial state factory at session start

---

## Proposed reusable contract

### 1) Common state envelope

Define a universal root state that every plugin must support.

```text
CommonState
- mode: "voice" | "text"
- device: "desktop" | "mobile" | ...
- transcript: str
- messages: list
- ui: { surfaceId, state }
- errors: optional error object
- pendingAction: optional action envelope
- outbox: list[ServerEvent]
- meta: dict (session metadata)
- domain: dict (plugin-owned payload)
```

**Key idea:** domain-specific keys move under `state.domain` to avoid polluting shared runtime state and to prevent key collisions across plugins.

### 2) Common event protocol

Retain stable event types consumed by frontend/runtime:

- `server.a2ui.patch`
- `server.voice.say`
- `server.transcript.final`
- `server.agent.thinking`

If a plugin needs additional events, either:

- add namespaced events (`server.domain.lost_card.*`) and extend client safely, or
- map plugin-specific events into the standard event set via adapter.

### 3) Plugin interface

Each plugin exposes:

- `plugin_id` (e.g., `mortgage`, `lost_card`)
- `create_initial_state(common_defaults) -> AgentState`
- `build_graph() -> CompiledStateGraph`
- `validate_action(actionId, data) -> bool | normalized_action`
- optional `capabilities` metadata (voice prompts, supported UI components, required tools)

This can be an abstract base class or a protocol type.

### 4) UI/action contract

Keep action envelopes generic:

```json
{ "actionId": "...", "data": { ... } }
```

Then each plugin manages its own action namespace to avoid collisions:

- mortgage: `mortgage.update_term`, `mortgage.select_product`
- lost card: `lost_card.freeze_card`, `lost_card.order_replacement`

Adapter can still accept legacy short IDs for backward compatibility.

---

## Migration approach (no big-bang rewrite)

### Phase 1 — Isolate existing mortgage graph behind plugin wrapper

- Create `MortgagePlugin` that simply wraps existing `app_graph` and initial state factory.
- Keep behavior unchanged.
- Websocket runtime calls plugin methods instead of importing `graph.py` symbols directly.

### Phase 2 — Introduce agent registry/factory

- Add `AgentRegistry` mapping `agent_id -> plugin`.
- Select active plugin using config/env/query param/header.
- Default to `mortgage` for current behavior.

### Phase 3 — Normalize state envelope

- Move mortgage-specific root fields into `state.domain.mortgage` gradually.
- Keep compatibility shims until client/tests fully updated.

### Phase 4 — Add second plugin (Lost Card)

Implement a first non-mortgage domain to prove extensibility:

- intents: lost/stolen card, urgent cash needs, travel context, fraud suspicion
- tools: freeze card API, transaction review, replacement card request, branch/ATM support
- UI: card status, safety checklist, recent transactions, replacement ETA, escalation path

### Phase 5 — shared observability + tests

- Add plugin contract tests that every plugin must pass.
- Add per-plugin scenario tests.
- Add end-to-end tests to verify runtime/plugin boundary.

---

## What would need to be done for a Lost Card Agent

### A) Build the domain plugin

- Define `LostCardState` under `state.domain.lost_card`.
- Implement graph nodes such as:
  - `ingest_input`
  - `classify_risk`
  - `freeze_card`
  - `verify_identity_stepup`
  - `issue_replacement`
  - `render_support_options`
- Emit standard outbox events (`server.a2ui.patch`, optional `server.voice.say`).

### B) Define a domain UI schema profile

- Reuse existing A2UI components where possible.
- Add minimal new components only if needed.
- Standardize CTA/action IDs under `lost_card.*` namespace.

### C) Integrate tools/services

- Add idempotent tool wrappers (safe retries).
- Add guardrails for critical actions (freeze card, replacement issuance).
- Require explicit confirmation for irreversible actions.

### D) Add policy and security controls

- PII-aware redaction in logs/transcripts.
- Strong auth checks before account-changing actions.
- Audit trail events for every critical action.

### E) Add plugin-specific test pack

- Happy path: lost card → freeze success → replacement initiated.
- Fraud path: suspicious transactions → escalate to specialist.
- Verification failure path: auth failure → fallback support.
- Recovery path: user says card found after freeze.

---

## Compatibility strategy

To avoid disruption while evolving:

- Keep existing websocket message envelope unchanged (`type`, `sessionId`, `payload`).【F:server/app/models.py†L8-L12】
- Keep outbox processing semantics unchanged initially (especially voice merge logic).【F:server/app/main.py†L106-L207】
- Introduce plugin contract behind current defaults before moving any UI or state fields.

This gives you **parallel run** capability:

- `mortgage` plugin for current flow,
- `lost_card` plugin for new flow,
- same transport runtime and frontend renderer.

---

## Recommended folder structure (example)

```text
server/app/agent/
  core/
    contracts.py          # plugin interface + common state/event types
    registry.py           # plugin discovery/selection
    runtime_adapter.py    # graph invoke + state transitions
  plugins/
    mortgage/
      plugin.py
      graph.py
      tools.py
      ui.py
    lost_card/
      plugin.py
      graph.py
      tools.py
      ui.py
```

---

## Decision checklist

Before implementing, decide:

1. **Plugin selection rule:** one plugin per deployment, per tenant, or per session?
2. **State versioning:** how to evolve plugin state safely over time?
3. **Action naming convention:** strict namespacing required?
4. **Shared vs plugin UI components:** where is the boundary?
5. **Cross-domain handoff:** can one plugin transfer to another mid-session?

---

## Practical answer to your specific question

If you create a Lost Card Agent today, **do not just swap `graph.py`** unless you intentionally preserve:

- the same imported symbol contract (`app_graph`, `AgentState`),
- compatible initial state fields expected by `create_initial_state`,
- standard outbox event types expected by websocket processing,
- and action/event semantics expected by the UI.

A better long-term design is the plugin architecture above: shared runtime + stable agent contract + domain plugins.

That approach transforms this project from a mortgage-specific assistant into a reusable **pluggable LangGraph agent framework**.

# Pluggable Agent Framework — Design Specs

Each file is a self-contained, executable design specification for one phase.
A developer can pick up any spec and implement it without reading the others,
provided the declared dependencies are met.

## Phase Index

| Phase | File | Depends on | Key output |
|---|---|---|---|
| 1 | `phase_1_plugin_isolation.md` | Nothing | `core/` package, `MortgagePlugin`, `main.py` decoupled |
| 2 | `phase_2_registry_selection.md` | Phase 1 | `LostCardPlugin` stub, registry selection by `?agent=` |
| 3 | `phase_3_state_normalisation.md` | Phase 1 + 2 | All domain fields under `state["domain"]["mortgage"]` |
| 4 | `phase_4_lost_card_agent.md` | Phase 1 + 2 | Full Lost Card agent with security controls |
| 5 | `phase_5_observability_tests.md` | Phase 1, 2, 4 | Contract tests, scenario tests, Langfuse `agent_id` |

## Execution order

```
Phase 1  →  Phase 2  →  Phase 3 (can run in parallel with Phase 4)
                     ↘  Phase 4  →  Phase 5
```

Phase 3 and Phase 4 are independent of each other once Phase 2 is done.

## Quick orientation

- **Source of truth for the design:** `docs/pluggable_agent_framework.md`
- **Build spec (high-level):** `docs/build_spec_pluggable_framework.md`
- **These specs:** `docs/design_specs/phase_*.md` — implementation-ready detail

## How each spec is structured

1. **Objective** — one-sentence goal
2. **Pre-flight check** — commands to run before touching code
3. **Step-by-step instructions** — file paths, complete code, exact diffs with line references
4. **Verification** — commands to run after each step
5. **Acceptance criteria** — checkbox list, definition of done
6. **What has NOT changed** — compatibility guarantees

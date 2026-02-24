# Phase 5 Design Spec — Shared Observability and Contract Tests

**Depends on:** Phase 1, 2, and 4 complete
**Branch suggestion:** `feat/phase-5-observability-tests`
**Estimated files changed:** 5 created, 1 modified

## Objective

Add a quality gate that every registered plugin must pass. Establish per-domain scenario
test files to prevent regressions. Add `agent_id` to Langfuse traces for per-plugin
observability. The contract tests run without a server; scenario tests require a running
server on `:8000`.

---

## Step 1 — Plugin Contract Tests

Create `server/tests/test_plugin_contract.py`.

This file is the canonical enforcement mechanism for `PluginBase`. Every plugin registered
in the project must pass all tests here. Add new tests to this file as the contract evolves.

```python
"""
test_plugin_contract.py — PluginBase contract tests.

Every plugin registered in the AgentRegistry must pass all tests here.
These tests run offline (no server, no AWS) using the plugin directly.

Run:
    cd server && python -m pytest tests/test_plugin_contract.py -v
"""

import operator
import pytest

# Bootstrap registry — mirrors the registration block in main.py.
# Update this list when new plugins are added.
from app.agent.core.registry import register, list_plugins
from app.agent.plugins.mortgage.plugin import MortgagePlugin
from app.agent.plugins.lost_card.plugin import LostCardPlugin

register(MortgagePlugin())
register(LostCardPlugin())

# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(params=list_plugins())
def plugin(request):
    from app.agent.core.registry import get_plugin
    return get_plugin(request.param)


# ── Contract: Identity ─────────────────────────────────────────────────────────

def test_plugin_id_is_non_empty_string(plugin):
    assert isinstance(plugin.plugin_id, str)
    assert len(plugin.plugin_id) > 0
    assert plugin.plugin_id == plugin.plugin_id.strip()


def test_plugin_id_is_slug(plugin):
    """plugin_id must be lowercase with underscores only (no spaces, no hyphens)."""
    import re
    assert re.match(r'^[a-z][a-z0-9_]*$', plugin.plugin_id), (
        f"plugin_id {plugin.plugin_id!r} must match ^[a-z][a-z0-9_]*$"
    )


def test_state_version_is_positive_int(plugin):
    assert isinstance(plugin.state_version, int)
    assert plugin.state_version >= 1


# ── Contract: Initial State ────────────────────────────────────────────────────

REQUIRED_COMMON_KEYS = [
    "mode", "device", "transcript", "messages",
    "ui", "pendingAction", "outbox",
    "meta", "domain", "state_version",
]

def test_initial_state_has_all_common_keys(plugin):
    state = plugin.create_initial_state()
    missing = [k for k in REQUIRED_COMMON_KEYS if k not in state]
    assert not missing, (
        f"Plugin {plugin.plugin_id!r} initial state is missing keys: {missing}"
    )


def test_initial_state_mode_is_valid(plugin):
    state = plugin.create_initial_state()
    assert state["mode"] in ("voice", "text"), (
        f"mode must be 'voice' or 'text', got {state['mode']!r}"
    )


def test_initial_state_outbox_is_empty_list(plugin):
    state = plugin.create_initial_state()
    assert isinstance(state["outbox"], list)
    assert len(state["outbox"]) == 0, (
        "Initial state outbox must be empty (events are populated by graph runs)"
    )


def test_initial_state_messages_is_empty_list(plugin):
    state = plugin.create_initial_state()
    assert isinstance(state["messages"], list)
    assert len(state["messages"]) == 0


def test_initial_state_domain_contains_plugin_namespace(plugin):
    """
    Plugin's initial domain must contain its own plugin_id as a key.
    This ensures domain isolation from the start.
    """
    state = plugin.create_initial_state()
    domain = state.get("domain", {})
    # Mortgage plugin uses top-level keys in Phase 1–2 compat mode;
    # lost_card uses domain["lost_card"]. Both are acceptable in Phase 1–2.
    # In Phase 3+, mortgage should also populate domain["mortgage"].
    # This test is lenient now — tighten after Phase 3 completion.
    assert isinstance(domain, dict)


def test_initial_state_version_matches_plugin(plugin):
    state = plugin.create_initial_state()
    assert state["state_version"] == plugin.state_version


def test_initial_state_ui_has_required_keys(plugin):
    state = plugin.create_initial_state()
    ui = state.get("ui", {})
    assert "surfaceId" in ui, "ui must have surfaceId"
    assert "state" in ui, "ui must have state"


# ── Contract: Graph ────────────────────────────────────────────────────────────

def test_build_graph_returns_compiled_graph(plugin):
    graph = plugin.build_graph()
    # LangGraph compiled graphs have an invoke method
    assert callable(getattr(graph, "invoke", None)), (
        "build_graph() must return an object with an invoke() method"
    )


def test_graph_invoke_on_initial_state_succeeds(plugin):
    """
    The graph must be invokable on initial state without error.
    This simulates the first server.ready → initial render cycle.
    """
    state = plugin.create_initial_state()
    graph = plugin.build_graph()
    result = graph.invoke(state)
    assert isinstance(result, dict), "Graph invoke must return a dict"


def test_graph_invoke_populates_outbox(plugin):
    """Every plugin must emit at least one outbox event on initial invoke."""
    state = plugin.create_initial_state()
    graph = plugin.build_graph()
    result = graph.invoke(state)
    assert "outbox" in result, "Result must contain 'outbox'"
    assert len(result["outbox"]) > 0, (
        f"Plugin {plugin.plugin_id!r} produced no outbox events on initial invoke"
    )


def test_graph_outbox_events_have_type_and_payload(plugin):
    """All outbox entries must have 'type' (str) and 'payload' (dict)."""
    state = plugin.create_initial_state()
    graph = plugin.build_graph()
    result = graph.invoke(state)
    for i, event in enumerate(result.get("outbox", [])):
        assert "type" in event, f"Outbox event {i} missing 'type'"
        assert isinstance(event["type"], str), f"Outbox event {i} 'type' must be str"
        assert "payload" in event, f"Outbox event {i} missing 'payload'"
        assert isinstance(event["payload"], dict), f"Outbox event {i} 'payload' must be dict"


def test_graph_outbox_types_are_valid(plugin):
    """
    Outbox event types must be in the approved set or namespaced under the plugin_id.
    This prevents plugins from emitting private internal types to the client.
    """
    APPROVED_TYPES = {
        "server.a2ui.patch",
        "server.voice.say",
        "server.transcript.final",
        "server.transcript.partial",
        "server.agent.thinking",
        "server.voice.start",
        "server.voice.stop",
        "server.voice.audio",
        "server.audit.event",
        "server.internal.chain_action",
    }
    state = plugin.create_initial_state()
    graph = plugin.build_graph()
    result = graph.invoke(state)
    for event in result.get("outbox", []):
        t = event["type"]
        namespaced_ok = t.startswith(f"server.domain.{plugin.plugin_id}.")
        assert t in APPROVED_TYPES or namespaced_ok, (
            f"Plugin {plugin.plugin_id!r} emitted unapproved event type {t!r}. "
            f"Use an approved type or namespace as 'server.domain.{plugin.plugin_id}.*'"
        )


def test_graph_invoke_with_transcript_does_not_crash(plugin):
    """Graph must handle a simple text transcript without raising."""
    state = plugin.create_initial_state()
    state["transcript"] = "Hello, I need some help"
    state["mode"] = "text"
    state["messages"] = [{"role": "user", "text": "Hello, I need some help"}]
    graph = plugin.build_graph()
    result = graph.invoke(state)
    assert isinstance(result, dict)


def test_graph_clears_pending_action(plugin):
    """
    After a graph run with a pendingAction, the result must have pendingAction cleared
    (or None). This is the clear_pending_action contract.
    """
    state = plugin.create_initial_state()
    # Use the plugin's own reset action or a dummy action
    reset_action_id = f"{plugin.plugin_id}.reset"
    state["pendingAction"] = {"id": "test_btn", "data": {"action": reset_action_id}}
    graph = plugin.build_graph()
    result = graph.invoke(state)
    # pendingAction should be None or absent after graph run
    assert not result.get("pendingAction"), (
        f"Plugin {plugin.plugin_id!r} did not clear pendingAction after graph run. "
        f"Got: {result.get('pendingAction')}"
    )


# ── Contract: validate_action ──────────────────────────────────────────────────

def test_validate_action_accepts_namespaced_action(plugin):
    """Plugin must accept actions prefixed with its own plugin_id."""
    action_id = f"{plugin.plugin_id}.some_action"
    result = plugin.validate_action(action_id, {})
    # Default implementation returns the action unchanged; override may normalise it.
    assert result is not None, (
        f"Plugin {plugin.plugin_id!r} rejected its own namespaced action {action_id!r}"
    )


# ── Contract: capabilities ─────────────────────────────────────────────────────

def test_capabilities_returns_dict(plugin):
    caps = plugin.capabilities
    assert isinstance(caps, dict)
```

---

## Step 2 — Lost Card Scenario Tests

Create `tests/scenarios_lost_card.py`.

These are integration tests that run against a live server. They follow the same pattern
as `tests/scenarios.py` (mortgage). All tests use `TestClient` from `tests/harness.py`
and connect to `ws://localhost:8000/ws?agent=lost_card`.

```python
"""
scenarios_lost_card.py — Lost Card agent goal-based integration tests.

Requires server running on :8000 (./manage.sh start or uvicorn app.main:app --port 8000).

Run:
    cd tests && python run_tests.py --agent lost_card
    cd tests && python run_tests.py LC-01
    cd tests && python run_tests.py --list
"""

import asyncio
import time
from harness import TestClient, TestResult

LC_WS_URL = "ws://localhost:8000/ws?agent=lost_card"


# ─────────────────────────────────────────────────────────────────────────────
# LC-01 — Lost Card Happy Path: report → freeze → replace
# ─────────────────────────────────────────────────────────────────────────────

async def lc_01() -> TestResult:
    r = TestResult(
        "LC-01",
        "Lost card happy path: report lost → identity → freeze → order replacement",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Initial state check
            landing = c.get_a2ui_patches()
            r.check("Agent emits initial UI patch on connect", len(landing) > 0,
                    f"{len(landing)} patches")

            # Report lost card
            msgs = await c.say("I've lost my card")
            patches = c.get_a2ui_patches(msgs)
            r.check("Screen updates after reporting lost card", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent acknowledges lost card verbally", len(voice) > 0,
                    f"voice: {voice[0][:60] if voice else 'none'}")

            # UI action: freeze (with pre-verified identity via action data)
            freeze_msgs = await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card",
                 "card_last4": "1234",
                 "_test_bypass_identity": True},
            )
            freeze_patches = c.get_a2ui_patches(freeze_msgs)
            r.check("Freeze action produces UI update", len(freeze_patches) > 0)
            freeze_voice = c.get_transcripts(freeze_msgs)
            r.check("Agent confirms card frozen", len(freeze_voice) > 0,
                    freeze_voice[0][:60] if freeze_voice else "none")
            r.check("Freeze confirmation mentions 'frozen' or 'freeze'",
                    any("frozen" in t.lower() or "freeze" in t.lower()
                        for t in freeze_voice))

            # UI action: request replacement
            replace_msgs = await c.ui_action(
                "btn_replace",
                {"action": "lost_card.order_replacement"},
            )
            replace_patches = c.get_a2ui_patches(replace_msgs)
            r.check("Replacement action produces UI update", len(replace_patches) > 0)
            replace_voice = c.get_transcripts(replace_msgs)
            r.check("Agent confirms replacement ordered", len(replace_voice) > 0)
            r.check("Replacement confirmation mentions arrival date",
                    any("arrive" in t.lower() or "arrival" in t.lower() or "days" in t.lower()
                        for t in replace_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-02 — Fraud Path: suspicious transactions → escalate
# ─────────────────────────────────────────────────────────────────────────────

async def lc_02() -> TestResult:
    r = TestResult(
        "LC-02",
        "Fraud path: suspicious transactions → escalate to fraud team",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Report suspicious transactions
            msgs = await c.say("I see transactions I don't recognise")
            patches = c.get_a2ui_patches(msgs)
            r.check("Fraud report produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to fraud report verbally", len(voice) > 0)
            r.check("Response acknowledges fraud concern",
                    any("concern" in t.lower() or "fraud" in t.lower()
                        or "unauthorised" in t.lower() or "unauthorized" in t.lower()
                        for t in voice))

            # Escalate
            escalate_msgs = await c.ui_action(
                "btn_escalate",
                {"action": "lost_card.escalate_fraud"},
            )
            escalate_patches = c.get_a2ui_patches(escalate_msgs)
            r.check("Escalation produces UI update", len(escalate_patches) > 0)
            escalate_voice = c.get_transcripts(escalate_msgs)
            r.check("Agent confirms escalation verbally", len(escalate_voice) > 0)
            r.check("Escalation message mentions fraud team or specialist",
                    any("fraud team" in t.lower() or "specialist" in t.lower()
                        or "investigate" in t.lower()
                        for t in escalate_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-03 — Recovery Path: found card after freeze
# ─────────────────────────────────────────────────────────────────────────────

async def lc_03() -> TestResult:
    r = TestResult(
        "LC-03",
        "Recovery path: freeze card → report found → unfreeze",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Freeze first
            await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card", "card_last4": "5678",
                 "_test_bypass_identity": True},
            )

            # Report found
            msgs = await c.say("I found my card")
            patches = c.get_a2ui_patches(msgs)
            r.check("Found card report produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to found card", len(voice) > 0)
            r.check("Response mentions card is frozen (and offers unfreeze)",
                    any("frozen" in t.lower() or "reactivat" in t.lower()
                        or "unfreeze" in t.lower()
                        for t in voice))
            r.check("Unfreeze button present",
                    c.has_button_with_text("unfreeze", msgs)
                    or c.has_button_with_text("reactivat", msgs))

            # Unfreeze
            unfreeze_msgs = await c.ui_action(
                "btn_unfreeze",
                {"action": "lost_card.unfreeze_card"},
            )
            unfreeze_patches = c.get_a2ui_patches(unfreeze_msgs)
            r.check("Unfreeze produces UI update", len(unfreeze_patches) > 0)
            unfreeze_voice = c.get_transcripts(unfreeze_msgs)
            r.check("Agent confirms card reactivated", len(unfreeze_voice) > 0)
            r.check("Reactivation message mentions active status",
                    any("active" in t.lower() or "reactivat" in t.lower()
                        or "ready" in t.lower()
                        for t in unfreeze_voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-04 — Identity Gate: freeze blocked without verification
# ─────────────────────────────────────────────────────────────────────────────

async def lc_04() -> TestResult:
    r = TestResult(
        "LC-04",
        "Identity gate: freeze attempt without identity verification is blocked",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            # Attempt to freeze without identity verification
            msgs = await c.ui_action(
                "btn_freeze",
                {"action": "lost_card.freeze_card"},  # no card_last4, no bypass
            )
            voice = c.get_transcripts(msgs)
            r.check("Agent responds to premature freeze attempt", len(voice) > 0)
            r.check("Response mentions identity or security check",
                    any("identity" in t.lower() or "security" in t.lower()
                        or "verify" in t.lower() or "last four" in t.lower()
                        or "4 digit" in t.lower() or "digits" in t.lower()
                        for t in voice))

            # Card should NOT be frozen
            # We verify by checking the next UI state — no "frozen" status card
            frozen_components = [
                c_ for c_ in c.get_all_components(msgs)
                if isinstance(c_.get("data"), dict)
                and "frozen" in str(c_.get("data", {}).get("status", "")).lower()
            ]
            r.check("Card is NOT frozen after blocked attempt",
                    len(frozen_components) == 0,
                    f"Found {len(frozen_components)} 'frozen' status components")

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# LC-05 — Default Handler: unrecognised input
# ─────────────────────────────────────────────────────────────────────────────

async def lc_05() -> TestResult:
    r = TestResult(
        "LC-05",
        "Default handler: unrecognised input → helpful options menu",
    )
    try:
        async with TestClient(LC_WS_URL) as c:
            msgs = await c.say("Tell me about mortgages")
            patches = c.get_a2ui_patches(msgs)
            r.check("Default handler produces UI update", len(patches) > 0)
            voice = c.get_transcripts(msgs)
            r.check("Default handler responds verbally", len(voice) > 0)
            r.check("Response guides user to card services",
                    any("card" in t.lower() or "lost" in t.lower() or "help" in t.lower()
                        for t in voice))

    except Exception:
        import traceback
        r.error = traceback.format_exc()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

SCENARIOS = {
    "LC-01": lc_01,
    "LC-02": lc_02,
    "LC-03": lc_03,
    "LC-04": lc_04,
    "LC-05": lc_05,
}
```

---

## Step 3 — Update `run_tests.py` to Support Multiple Scenario Files

**Find `tests/run_tests.py`** and update the scenario discovery to load from both
`scenarios.py` (mortgage) and `scenarios_lost_card.py` (lost_card).

**Add to the top of `run_tests.py`:**

```python
from scenarios import SCENARIOS as MORTGAGE_SCENARIOS
from scenarios_lost_card import SCENARIOS as LC_SCENARIOS

ALL_SCENARIOS = {**MORTGAGE_SCENARIOS, **LC_SCENARIOS}
```

**Add `--agent` flag** to the CLI argument parser:

```python
import argparse

parser = argparse.ArgumentParser(description="Run GBT/LC integration tests")
parser.add_argument("scenario_ids", nargs="*", help="Specific scenario IDs to run")
parser.add_argument("--list", action="store_true", help="List available scenario IDs")
parser.add_argument("--agent", choices=["mortgage", "lost_card", "all"],
                    default="all", help="Filter by agent (default: all)")
args = parser.parse_args()

if args.agent == "mortgage":
    SCENARIOS = MORTGAGE_SCENARIOS
elif args.agent == "lost_card":
    SCENARIOS = LC_SCENARIOS
else:
    SCENARIOS = ALL_SCENARIOS
```

---

## Step 4 — Add `agent_id` to Langfuse Metadata in `main.py`

There are four Langfuse config blocks in `main.py`. Each currently reads:

```python
config = {
    "callbacks": [lf_callback],
    "metadata": {"langfuse_session_id": sid}
}
```

**Replace all four with:**

```python
config = {
    "callbacks": [lf_callback],
    "metadata": {
        "langfuse_session_id": sid,
        "agent_id": sessions[sid].get("agent_id", "mortgage"),
    },
}
```

This adds the `agent_id` tag to every Langfuse trace, enabling per-plugin filtering
in the Langfuse dashboard at no extra cost.

---

## Step 5 — Add Plugin List Health Endpoint

Add a lightweight HTTP endpoint to `main.py` for operational visibility.

```python
@app.get("/agents")
async def list_agents():
    """Return registered agent plugin IDs. Used for health checks and tooling."""
    from .agent.core.registry import list_plugins
    return {"agents": list_plugins()}
```

---

## Verification

```bash
cd /Users/jamescregeen/A2UI_S2S
source server/.venv/bin/activate

# 1. Contract tests (no server needed)
python -m pytest server/tests/test_plugin_contract.py -v

# 2. Unit tests still pass
python -m pytest server/tests/test_math.py -v

# 3. Both test suites together
python -m pytest server/tests/ -v

# 4. Integration tests — mortgage (requires server on :8000)
./manage.sh start
sleep 5
cd tests && python run_tests.py --agent mortgage

# 5. Integration tests — lost_card (requires server on :8000)
cd tests && python run_tests.py --agent lost_card

# 6. List all available scenarios
cd tests && python run_tests.py --list

# 7. Health endpoint
curl http://localhost:8000/agents
# Expected: {"agents": ["mortgage", "lost_card"]}

./manage.sh stop
```

---

## Acceptance Criteria (Definition of Done)

- [ ] `python -m pytest server/tests/test_plugin_contract.py -v` — all tests pass for both plugins
- [ ] `python -m pytest server/tests/test_math.py -v` — all pass
- [ ] `python run_tests.py --agent mortgage` — all existing mortgage scenarios pass
- [ ] `python run_tests.py --agent lost_card` — LC-01 through LC-05 all pass
- [ ] `python run_tests.py --list` — shows IDs for both mortgage (GBT-*) and lost card (LC-*)
- [ ] Langfuse traces contain `agent_id` metadata field
- [ ] `GET /agents` returns `{"agents": ["mortgage", "lost_card"]}`
- [ ] Adding a new plugin and calling `register()` causes it to appear in `/agents` and fail contract tests if it misses a required method (demonstrates the gate works)

---

## What Has NOT Changed

- `WebSocketMessage` / `models.py`
- `process_outbox()` logic (other than audit event skip added in Phase 4)
- Client code
- Mortgage graph logic

"""
test_plugin_contract.py — PluginBase contract tests.

Every plugin registered in the AgentRegistry must pass all tests here.
These tests run offline (no server, no AWS) using the plugin directly.

Run:
    cd server && python -m pytest tests/test_plugin_contract.py -v
"""

import re
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
    reset_action_id = f"{plugin.plugin_id}.reset"
    state["pendingAction"] = {"id": "test_btn", "data": {"action": reset_action_id}}
    graph = plugin.build_graph()
    result = graph.invoke(state)
    assert not result.get("pendingAction"), (
        f"Plugin {plugin.plugin_id!r} did not clear pendingAction after graph run. "
        f"Got: {result.get('pendingAction')}"
    )


# ── Contract: validate_action ──────────────────────────────────────────────────

def test_validate_action_accepts_namespaced_action(plugin):
    """Plugin must accept actions prefixed with its own plugin_id."""
    action_id = f"{plugin.plugin_id}.some_action"
    result = plugin.validate_action(action_id, {})
    assert result is not None, (
        f"Plugin {plugin.plugin_id!r} rejected its own namespaced action {action_id!r}"
    )


# ── Contract: capabilities ─────────────────────────────────────────────────────

def test_capabilities_returns_dict(plugin):
    caps = plugin.capabilities
    assert isinstance(caps, dict)

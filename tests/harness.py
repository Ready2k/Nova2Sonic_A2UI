"""
harness.py — WebSocket test client for the Barclays Mortgage Assistant.

Provides:
  - TestClient: connect, send turns (text/UI), collect responses
  - Assertion helpers that produce clear pass/fail messages
"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional
import websockets

WS_URL = "ws://localhost:8000/ws"
DEFAULT_TIMEOUT = 6.0   # seconds to wait for expected messages


@dataclass
class Message:
    type: str
    payload: dict
    received_at: float = field(default_factory=time.time)


class TestClient:
    """Async context manager that manages a WS session and provides test helpers."""

    def __init__(self, ws_url: str = WS_URL):
        self.ws_url = ws_url
        self.ws = None
        self.messages: list[Message] = []
        self._start_time: float = 0.0

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url)
        self._start_time = time.time()
        # Drain the initial server.ready + a2ui.patch
        await self._drain(timeout=3.0)
        return self

    async def __aexit__(self, *_):
        if self.ws:
            await self.ws.close()

    # ------------------------------------------------------------------ helpers

    async def _recv_one(self, timeout: float) -> Optional[Message]:
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            data = json.loads(raw)
            msg = Message(type=data.get("type", ""), payload=data.get("payload") or {})
            self.messages.append(msg)
            return msg
        except asyncio.TimeoutError:
            return None

    async def _drain(self, timeout: float = 2.0) -> list[Message]:
        """Collect all messages until a timeout occurs."""
        collected = []
        while True:
            msg = await self._recv_one(timeout=timeout)
            if msg is None:
                break
            collected.append(msg)
        return collected

    async def send_raw(self, payload: dict):
        await self.ws.send(json.dumps(payload))

    # ------------------------------------------------------------------ actions

    async def click_category(self, category: str, button_id: str) -> list[Message]:
        """Simulate clicking a mortgage category button."""
        await self.send_raw({
            "type": "client.ui.action",
            "sessionId": "test",
            "payload": {"id": button_id, "data": {"action": "select_category", "category": category}}
        })
        return await self._drain(timeout=DEFAULT_TIMEOUT)

    async def say(self, text: str) -> list[Message]:
        """Simulate a voice utterance via client.text (runs full graph pipeline)."""
        await self.send_raw({
            "type": "client.text",
            "sessionId": "test",
            "payload": {"text": text}
        })
        return await self._drain(timeout=DEFAULT_TIMEOUT)

    async def ui_action(self, action_id: str, data: dict) -> list[Message]:
        """Send a generic UI action."""
        await self.send_raw({
            "type": "client.ui.action",
            "sessionId": "test",
            "payload": {"id": action_id, "data": data}
        })
        return await self._drain(timeout=DEFAULT_TIMEOUT)

    async def reset(self) -> list[Message]:
        return await self.ui_action("reset_flow", {"action": "reset_flow"})

    # ------------------------------------------------------------------ query helpers

    def get_a2ui_patches(self, msgs: list[Message] = None) -> list[dict]:
        src = msgs if msgs is not None else self.messages
        return [m.payload for m in src if m.type == "server.a2ui.patch"]

    def get_transcripts(self, msgs: list[Message] = None) -> list[str]:
        src = msgs if msgs is not None else self.messages
        return [m.payload.get("text", "") for m in src
                if m.type == "server.transcript.final" and m.payload.get("role") == "assistant"]

    def get_all_components(self, msgs: list[Message] = None) -> list[dict]:
        """Flatten all components from the latest a2ui patch."""
        patches = self.get_a2ui_patches(msgs)
        if not patches:
            return []
        latest = patches[-1]
        return latest.get("updateComponents", {}).get("components", [])

    def find_component(self, comp_id: str, msgs: list[Message] = None) -> Optional[dict]:
        for c in self.get_all_components(msgs):
            if c.get("id") == comp_id:
                return c
        return None

    def has_component_type(self, comp_type: str, msgs: list[Message] = None) -> bool:
        return any(c.get("component") == comp_type for c in self.get_all_components(msgs))

    def get_header(self, msgs: list[Message] = None) -> str:
        c = self.find_component("header", msgs)
        return c.get("text", "") if c else ""

    def get_gauge_value(self, msgs: list[Message] = None) -> Optional[float]:
        for c in self.get_all_components(msgs):
            if c.get("component") == "Gauge":
                return c.get("value")
        return None

    def count_product_cards(self, msgs: list[Message] = None) -> int:
        return sum(1 for c in self.get_all_components(msgs)
                   if c.get("component") == "Card" or "product" in c.get("id", ""))

    def has_button_with_text(self, text: str, msgs: list[Message] = None) -> bool:
        return any(
            c.get("component") == "Button" and text.lower() in (c.get("text") or "").lower()
            for c in self.get_all_components(msgs)
        )

    def elapsed_ms(self, since: float) -> float:
        return (time.time() - since) * 1000


# ------------------------------------------------------------------ assert helpers

class TestResult:
    def __init__(self, test_id: str, goal: str):
        self.test_id = test_id
        self.goal = goal
        self.checks: list[tuple[str, bool, str]] = []  # (description, passed, detail)
        self.error: Optional[str] = None

    def check(self, description: str, condition: bool, detail: str = ""):
        self.checks.append((description, condition, detail))

    def passed(self) -> bool:
        return self.error is None and all(c[1] for c in self.checks)

    def summary(self) -> str:
        status = "✅ PASS" if self.passed() else "❌ FAIL"
        lines = [f"\n{'='*60}", f"{status}  {self.test_id}", f"Goal: {self.goal}", ""]
        if self.error:
            lines.append(f"  💥 ERROR: {self.error}")
        for desc, ok, detail in self.checks:
            icon = "  ✅" if ok else "  ❌"
            lines.append(f"{icon} {desc}" + (f"  ({detail})" if detail else ""))
        return "\n".join(lines)

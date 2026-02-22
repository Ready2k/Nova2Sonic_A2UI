"""
chat_endpoint.py — A standalone WebSocket endpoint for the Chat-only mode.

- Zero dependency on NovaSonicSession or any audio code.
- Accepts: client.hello, client.text, client.ui.action
- Emits:   server.ready, server.a2ui.patch, server.transcript.final, server.agent.thinking
- Uses the same LangGraph app_graph for intent extraction and UI rendering.
"""
import asyncio
import logging
from fastapi import WebSocket, WebSocketDisconnect
from .models import WebSocketMessage
from .agent.graph import app_graph, AgentState

logger = logging.getLogger(__name__)

# Shared session store — keyed by id(websocket) so it's separate from the
# voice sessions dict in main.py
_chat_sessions: dict[str, dict] = {}


def _create_chat_state() -> AgentState:
    return {
        "mode": "text",
        "transcript": "",
        "messages": [],
        "intent": {
            "propertyValue": None,
            "loanBalance": None,
            "fixYears": None,
            "termYears": 25,
            "category": None,
        },
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "ui": {"surfaceId": "main", "state": "LOADING"},
        "errors": None,
        "pendingAction": None,
        "outbox": [],
        "existing_customer": None,
        "property_seen": None,
    }


async def _send(ws: WebSocket, sid: str, msg_type: str, payload: dict | None = None):
    try:
        msg = WebSocketMessage(type=msg_type, sessionId=sid, payload=payload)
        await ws.send_text(msg.model_dump_json())
    except Exception as e:
        logger.error(f"[chat] send error: {e}")


async def _flush_outbox(ws: WebSocket, sid: str):
    """Drain the outbox: send a2ui patches and transcript messages; ignore voice."""
    state = _chat_sessions[sid]["state"]
    outbox = state.get("outbox", [])

    # Pass 1: non-voice events (a2ui patch etc.) — immediate
    for event in outbox:
        if event["type"] != "server.voice.say":
            logger.info(f"[chat] emitting {event['type']}")
            await _send(ws, sid, event["type"], event.get("payload"))

    # Pass 2: voice.say → only emit as a text transcript (no TTS)
    for event in outbox:
        if event["type"] == "server.voice.say":
            text = event.get("payload", {}).get("text", "")
            logger.info(f"[chat] voice.say → transcript: {text[:60]}")
            await _send(ws, sid, "server.transcript.final",
                        {"text": text, "role": "assistant"})

    state["outbox"] = []


async def chat_ws_endpoint(websocket: WebSocket):
    """
    Dedicated chat-only WebSocket handler.
    Mount at:  @app.websocket("/ws/chat")
    """
    await websocket.accept()
    sid = f"chat_{id(websocket)}"
    _chat_sessions[sid] = {"state": _create_chat_state()}

    try:
        await _send(websocket, sid, "server.ready")

        # Initial graph run → landing category screen
        initial = await asyncio.to_thread(app_graph.invoke, _chat_sessions[sid]["state"])
        # Strip voice from initial outbox (silent landing)
        initial["outbox"] = [e for e in initial.get("outbox", [])
                             if e["type"] != "server.voice.say"]
        _chat_sessions[sid]["state"] = initial
        await _flush_outbox(websocket, sid)

        while True:
            raw = await websocket.receive_text()
            try:
                event = WebSocketMessage.model_validate_json(raw)
            except Exception as e:
                logger.error(f"[chat] bad frame: {e}")
                continue

            msg_type = event.type
            payload = event.payload or {}
            logger.info(f"[chat] recv {msg_type}")

            if msg_type == "client.hello":
                continue  # already handled

            elif msg_type == "client.text":
                text = payload.get("text", "").strip()
                if not text:
                    continue

                current = _chat_sessions[sid]["state"]
                current["transcript"] = text
                current["mode"] = "text"
                current["messages"].append({"role": "user", "text": text})

                await _send(websocket, sid, "server.transcript.final",
                            {"text": text, "role": "user"})
                await _send(websocket, sid, "server.agent.thinking",
                            {"state": "extracting_intent"})

                try:
                    res = await asyncio.to_thread(app_graph.invoke, current)
                    _chat_sessions[sid]["state"] = res
                    await _flush_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"[chat] graph error: {e}")
                    traceback.print_exc()
                    await _send(websocket, sid, "server.agent.thinking", {"state": "idle"})

            elif msg_type == "client.ui.action":
                action_id = payload.get("id")
                data = payload.get("data", {})
                logger.info(f"[chat] UI action {action_id} data={data}")

                try:
                    current = _chat_sessions[sid]["state"]
                    current["pendingAction"] = {"id": action_id, "data": data}
                    res = await asyncio.to_thread(app_graph.invoke, current)
                    _chat_sessions[sid]["state"] = res
                    await _flush_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"[chat] UI action error '{action_id}': {e}")
                    traceback.print_exc()

    except WebSocketDisconnect:
        logger.info(f"[chat] session {sid} disconnected")
    finally:
        _chat_sessions.pop(sid, None)

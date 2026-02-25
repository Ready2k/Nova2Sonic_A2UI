import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from .models import WebSocketMessage, ActionPayload
from .agent.core.registry import get_plugin
from .agent.core.runtime_adapter import invoke_graph
from .agent.plugin_loader import load_all_plugins
from .nova_sonic import NovaSonicSession
from .langfuse_util import get_langfuse_callback

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

app = FastAPI(title="Barclays Mortgage Assistant")

# Auto-discover and register all plugins found under app.agent.plugins.
load_all_plugins()

# Admin / import API
from .admin import router as admin_router  # noqa: E402
app.include_router(admin_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: Dict[str, dict] = {}

# Initial state is now owned by each plugin via plugin.create_initial_state().
# See plugins/mortgage/plugin.py and plugins/lost_card/plugin.py.

async def send_msg(websocket: WebSocket, session_id: str, msg_type: str, payload: dict = None):
    try:
        msg = WebSocketMessage(type=msg_type, sessionId=session_id, payload=payload)
        await websocket.send_text(msg.model_dump_json())
    except Exception as e:
        logger.error(f"Cannot send to ws: {e}")

async def run_tts_inline(websocket: WebSocket, session_id: str, text_to_speak: str):
    """Run Node TTS synchronously (awaited) so the WS stays open for the full audio stream."""
    try:
        logger.info(f"[TTS] Starting for text: {text_to_speak[:60]}")
        proc = await asyncio.create_subprocess_exec(
            "node", "nova_sonic_tts.mjs", text_to_speak,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        async def log_stderr(stderr):
            while True:
                line = await stderr.readline()
                if not line: break
                logger.debug(f"[TTS DEBUG (stderr)] {line.decode().strip()}")

        stderr_task = asyncio.create_task(log_stderr(proc.stderr))

        chunk_count = 0
        while True:
            line = await proc.stdout.readline()
            if not line: 
                logger.info(f"[TTS] No more output, received {chunk_count} audio chunks")
                break
            decoded = line.decode().strip()
            if decoded.startswith("AUDIO_CHUNK:"):
                chunk_count += 1
                chunk_data = decoded.split("AUDIO_CHUNK:")[1]
                logger.info(f"[TTS] Sending audio chunk {chunk_count}, size: {len(chunk_data)}")
                await send_msg(websocket, session_id, "server.voice.audio", {"data": chunk_data})
        
        # Signal stop to client immediately once stdout ends
        await send_msg(websocket, session_id, "server.voice.stop", {"sid": session_id})
        
        await proc.wait()
        await stderr_task

    except Exception as e:
        logger.error(f"TTS fallback failed: {e}")
    finally:
        if session_id in sessions:
            logger.info(f"[TTS] Setting voice_playing to False")
            sessions[session_id]["voice_playing"] = False
        logger.info(f"[TTS] Sending voice.stop for {session_id}")
        await send_msg(websocket, session_id, "server.voice.stop", {})


async def start_sonic_stt(websocket: WebSocket, sid: str):
    """Pre-warms or starts the Nova Sonic STT process."""
    session_data = sessions.get(sid)
    if not session_data:
        return None
        
    if session_data.get("sonic"):
        return session_data["sonic"]

    # Verbatim STT prompt optimized for short mortgage-related responses
    stt_system_prompt = (
        "You are a verbatim speech-to-text transcriber for a mortgage assistant. "
        "Output ONLY the exact words spoken by the user. If they say 'yes', 'no', 'I do', or 'yep', output exactly that. "
        "Do not add punctuation, do not add commentary, and do not try to fix the user's grammar."
    )

    async def _on_text_chunk(text, is_user=False, is_final=False):
        await handle_text_chunk(websocket, sid, text, is_user=True, is_final=is_final)

    async def _handle_finished():
        await handle_finished_for_sid(websocket, sid)

    sonic = NovaSonicSession(
        on_audio_chunk=lambda x, **kw: None,
        on_text_chunk=_on_text_chunk,
        on_finished=_handle_finished
    )
    
    session_data["sonic"] = sonic
    session_data["user_transcripts"] = []

    try:
        await sonic.start_session()
        await sonic.start_audio_input(system_prompt=stt_system_prompt)
        return sonic
    except Exception as e:
        logger.error(f"Failed to start Nova Sonic session: {e}", exc_info=True)
        return None


async def handle_text_chunk(websocket: WebSocket, sid: str, text: str, is_user=False, is_final=False):
    session_data = sessions.get(sid)
    if not session_data: return
    
    if is_user:
        if is_final:
            logger.info(f"FINAL USER TEXT RECEIVED: {text}")
            session_data["user_transcripts"] = [text]
        else:
            logger.debug(f"APPENDING USER TEXT: {text}")
            session_data["user_transcripts"].append(text)
        # Send partial transcript to client for real-time feedback
        await send_msg(websocket, sid, "server.transcript.partial", {"text": text})
    else:
        if "assist_buffer" not in session_data: session_data["assist_buffer"] = []
        session_data["assist_buffer"].append(text)
        await send_msg(websocket, sid, "server.transcript.final", {"text": text, "role": "assistant"}) 


async def handle_finished_for_sid(websocket: WebSocket, sid: str):
    session_data = sessions.get(sid)
    if not session_data: return
    
    current_state = session_data["state"]
    if session_data.get("handling_finished"):
        logger.warning(f"Nova Sonic: handle_finished already in progress for {sid}, skipping duplicate.")
        return
    
    session_data["handling_finished"] = True
    try:
        assist_text = "".join(session_data.get("assist_buffer", [])).strip()
        if assist_text:
            current_state["messages"].append({"role": "assistant", "text": assist_text})
            session_data["assist_buffer"] = []

        full_transcript = "".join(session_data["user_transcripts"]).strip()
        # Continue even if empty to detect silence/trouble
        session_data["user_transcripts"] = []
        
        await send_msg(websocket, sid, "server.transcript.final", {"text": full_transcript, "role": "user"})
        
        current_state["transcript"] = full_transcript
        current_state["mode"] = "voice"
        if full_transcript:
            current_state["messages"].append({"role": "user", "text": full_transcript})
        
        await send_msg(websocket, sid, "server.agent.thinking", {"state": "extracting_intent"})
        
        try:
            lf_callback = get_langfuse_callback()
            config = {
                "callbacks": [lf_callback],
                "metadata": {
                    "langfuse_session_id": sid,
                    "agent_id": session_data.get("agent_id", "mortgage"),
                },
            }
            _plugin = get_plugin(session_data.get("agent_id", "mortgage"))
            res = await invoke_graph(_plugin, current_state, config)
            if sid in sessions:
                sessions[sid]["state"] = res
            await process_outbox(websocket, sid)
        except Exception as e:
            import traceback
            logger.error(f"Error in LangGraph matching (voice/finished): {e}")
            traceback.print_exc()
    finally:
        session_data["handling_finished"] = False


async def process_outbox(websocket: WebSocket, sid: str):
    session_data = sessions.get(sid)
    if not session_data:
        return

    if session_data.get("processing_outbox"):
        logger.warning(f"Nova Sonic: process_outbox already in progress for {sid}, skipping.")
        return
    
    session_data["processing_outbox"] = True
    try:
        state = session_data["state"]
        outbox = state.get("outbox", [])
        if not outbox:
            return
            
        # Clear outbox immediately to prevent race conditions
        state["outbox"] = []
        
        voice_say_count = len([e for e in outbox if e["type"] == "server.voice.say"])
        logger.info(f"[process_outbox] Processing {len(outbox)} events, {voice_say_count} voice.say events")
        
        # Pass 1: send ALL non-voice events immediately (a2ui.patch, transcript, etc.)
        _SKIP_TYPES = {"server.voice.say", "server.audit.event", "server.internal.chain_action"}
        assistant_transcripts_sent = set()
        for event in outbox:
            if event["type"] not in _SKIP_TYPES:
                logger.info(f"Emitting from outbox: {event['type']}")
                payload = event.get("payload", {}) or {}
                if event["type"] == "server.a2ui.patch":
                    payload["showSupport"] = (
                        state.get("domain", {})
                        .get("mortgage", {})
                        .get("show_support", False)
                        or state.get("domain", {})
                        .get("lost_card", {})
                        .get("show_support", False)
                    )
                await send_msg(websocket, sid, event["type"], payload)

                if event["type"] == "server.internal.handoff":
                    new_agent_id = payload.get("agent_id")
                    if new_agent_id:
                        logger.info(f"--- HANDOFF: Switching session {sid} to agent: {new_agent_id} ---")
                        try:
                            new_plugin = get_plugin(new_agent_id)
                            session_data["agent_id"] = new_agent_id
                            
                            # Re-initialize state for the new plugin but keep CommonState envelope items
                            fresh_state = new_plugin.create_initial_state()
                            for key in ["mode", "device", "messages", "meta"]:
                                if key in state:
                                    fresh_state[key] = state[key]
                            
                            # Merge existing messages if any
                            session_data["state"] = fresh_state
                            # Important: the current loop continues, but the session is now 're-homed'
                        except Exception as hex:
                            logger.error(f"Handoff failed: {hex}")

                if event["type"] == "server.transcript.final":
                    payload = event.get("payload") or {}
                    if payload.get("role") == "assistant":
                        txt = (payload.get("text") or "").strip()
                        if txt:
                            assistant_transcripts_sent.add(txt)

        # Pass 2: handle voice.say last.
        # Some graph turns emit multiple voice.say events for a single assistant response
        # (for example sentence-by-sentence or full-text + sentence chunks).
        # Merge with de-duplication so the user hears the full response once.
        voice_text_parts = []
        for event in outbox:
            if event["type"] != "server.voice.say":
                continue

            text_part = (event.get("payload", {}).get("text", "") or "").strip()
            if not text_part:
                continue

            if not voice_text_parts:
                voice_text_parts.append(text_part)
                continue

            merged_so_far = " ".join(voice_text_parts)
            # Skip exact or contained duplicates.
            if text_part == merged_so_far or text_part in merged_so_far:
                continue
            # If a later segment contains everything we've seen, prefer it.
            if merged_so_far in text_part:
                voice_text_parts = [text_part]
                continue

            voice_text_parts.append(text_part)

        text_to_speak = " ".join(voice_text_parts).strip()
        if text_to_speak:
            logger.info(f"Emitting combined server.voice.say ({len(voice_text_parts)} merged parts) -> '{text_to_speak[:60]}'")

            # Echo assistant transcript only if it hasn't already been emitted upstream.
            if text_to_speak not in assistant_transcripts_sent:
                await send_msg(websocket, sid, "server.transcript.final", {"text": text_to_speak, "role": "assistant"})

            # Skip TTS if client is in Text Only mode
            if state.get("mode") == "text":
                logger.info("Skipping TTS (client in Text Only mode)")
            # Send TTS if not already playing voice from another source
            elif not session_data.get("voice_playing"):
                logger.info(f"[TTS] Starting TTS for text: {text_to_speak[:40]}")
                session_data["voice_playing"] = True
                # Notify client immediately
                await send_msg(websocket, sid, "server.voice.start", {})
                
                # PRE-WARM STT: Start the STT process in background while TTS is playing
                asyncio.create_task(start_sonic_stt(websocket, sid))
                
                # Fire TTS as background task
                tts_task = asyncio.create_task(run_tts_inline(websocket, sid, text_to_speak))
                session_data["tts_task"] = tts_task
            else:
                logger.warning("[TTS] Skipping TTS - voice already playing")
        
        # Clear thinking state
        await send_msg(websocket, sid, "server.agent.thinking", {"state": "idle"})
    finally:
        session_data["processing_outbox"] = False


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, agent: str = "mortgage"):
    # Validate agent_id before accepting so we can reject with a close code.
    try:
        plugin = get_plugin(agent)
    except KeyError as exc:
        await websocket.accept()
        await websocket.close(code=4000, reason=str(exc))
        logger.error("[WebSocket] Unknown agent_id=%r, closing with 4000", agent)
        return

    await websocket.accept()
    session_id = f"sess_{id(websocket)}"
    logger.info("[WebSocket] New connection: %s (agent=%s)", session_id, agent)

    sessions[session_id] = {
        "agent_id": agent,
        "state": plugin.create_initial_state(),
        "voice_playing": False,
        "tts_task": None,
        "sonic": None,
        "user_transcripts": []
    }
    
    try:
        await send_msg(websocket, session_id, "server.ready")
        
        # Trigger initial UI rendering (landing category screen)
        # Strip all voice.say — the landing grid is visual-only.
        # First voice fires when the user clicks a category button.
        lf_callback = get_langfuse_callback()
        config = {
            "callbacks": [lf_callback],
            "metadata": {
                "langfuse_session_id": session_id,
                "agent_id": sessions[session_id].get("agent_id", "mortgage"),
            },
        }
        initial_res = await invoke_graph(plugin, sessions[session_id]["state"], config)
        # Suppress any voice on initial load to avoid double-audio from React StrictMode remounts
        initial_res["outbox"] = [e for e in initial_res.get("outbox", []) if e["type"] != "server.voice.say"]
        sessions[session_id]["state"] = initial_res
        await process_outbox(websocket, session_id)
        
        while True:
            data = await websocket.receive_text()
            logger.info(f"--- Raw WS data len: {len(data)}")
            try:
                event = WebSocketMessage.model_validate_json(data)
            except Exception as e:
                logger.error(f"WebSocketMessage validation failed: {e}. Data: {data[:100]}")
                continue
                
            msg_type = event.type
            payload = event.payload or {}
            logger.info(f"--- Parsed Type: {msg_type}")
            sid = session_id
            session_data = sessions.get(sid)
            if not session_data: continue
            
            state = session_data["state"]

                
            if msg_type == "client.audio.start":
                # Re-use or start the sonic session
                await start_sonic_stt(websocket, sid)
                
            elif msg_type == "client.audio.chunk":
                if session_data["sonic"]:
                    b64 = payload.get("data")
                    if b64:
                        if "chunk_count" not in session_data:
                            session_data["chunk_count"] = 0
                        session_data["chunk_count"] += 1
                        if session_data["chunk_count"] % 10 == 0:
                            logger.info(f"--- Received {session_data['chunk_count']} audio chunks so far ---")
                        await session_data["sonic"].send_audio_chunk(b64)
                        
            elif msg_type == "client.audio.stop":
                if session_data["sonic"]:
                    # Run STT as a background task so the message loop is not blocked
                    # (STT subprocess can take several seconds)
                    asyncio.create_task(session_data["sonic"].end_audio_input())

            elif msg_type == "client.audio.interrupt":
                # Cancel any in-flight TTS subprocess
                if session_data.get("tts_task") and not session_data["tts_task"].done():
                    session_data["tts_task"].cancel()
                    session_data["tts_task"] = None
                if session_data["sonic"]:
                    await session_data["sonic"].end_session()
                    session_data["sonic"] = None
                session_data["voice_playing"] = False
                await send_msg(websocket, sid, "server.voice.stop")
                logger.info(f"--- Voice interrupted and stopped for {sid} ---")
                
            elif msg_type == "client.text":
                transcript = payload.get("text", "")
                image_b64 = payload.get("image")
                
                state["transcript"] = transcript
                state["mode"] = "text"
                
                # If Nova Sonic was active, kill it — we are in Text Only mode now
                if session_data.get("sonic"):
                    try:
                        asyncio.create_task(session_data["sonic"].end_session())
                        session_data["sonic"] = None
                    except: pass
                
                msg_obj = {"role": "user", "text": transcript}
                if image_b64:
                    if image_b64.startswith("data:"): image_b64 = image_b64.split(",", 1)[1]
                    msg_obj["image"] = image_b64
                state["messages"].append(msg_obj)
                
                await send_msg(websocket, sid, "server.transcript.final", {"text": transcript, "image": image_b64})
                await send_msg(websocket, sid, "server.agent.thinking", {"state": "rendering_ui"})
                
                try:
                    lf_callback = get_langfuse_callback()
                    config = {
                        "callbacks": [lf_callback],
                        "metadata": {
                            "langfuse_session_id": sid,
                            "agent_id": sessions[sid].get("agent_id", "mortgage"),
                        },
                    }
                    _plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
                    res = await invoke_graph(_plugin, state, config)
                    if sid in sessions:
                        sessions[sid]["state"] = res
                    await process_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"Error in LangGraph matching (text): {e}")
                    traceback.print_exc()
                    await send_msg(websocket, sid, "server.agent.thinking", {"state": "idle"})
                    
            elif msg_type == "client.ui.action":
                action_id = payload.get("id")
                data = payload.get("data", {})
                
                logger.info(f"Received UI Action: {action_id} with data: {data}")
                
                try:
                    # Always use latest state (stale closure guard)
                    current_state = sessions[sid]["state"]
                    current_state["pendingAction"] = {"id": action_id, "data": data}
                    
                    try:
                        lf_callback = get_langfuse_callback()
                        config = {
                            "callbacks": [lf_callback],
                            "metadata": {
                                "langfuse_session_id": sid,
                                "agent_id": sessions[sid].get("agent_id", "mortgage"),
                            },
                        }
                        _plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
                        res = await invoke_graph(_plugin, current_state, config)
                        if sid in sessions:
                            sessions[sid]["state"] = res
                        await process_outbox(websocket, sid)
                    except Exception as e:
                        import traceback
                        logger.error(f"Error in UI action: {e}")
                        traceback.print_exc()
                        await send_msg(websocket, sid, "server.agent.thinking", {"state": "idle"})
                except Exception as e:
                    import traceback
                    logger.error(f"Error handling UI action '{action_id}': {e}")
                    traceback.print_exc()
                    
            elif msg_type == "client.mode.update":
                new_mode = payload.get("mode")
                new_device = payload.get("device")
                old_device = state.get("device", "desktop")
                logger.info(f"Mode/Device update from client: mode={new_mode}, device={new_device}")
                
                if new_mode:
                    state["mode"] = new_mode
                if new_device:
                    state["device"] = new_device

                # If the device changed, re-render the current screen via a full graph invoke.
                if new_device and new_device != old_device:
                    # Clear transcript and pendingAction so start_router does not
                    # re-interpret the last user message — we only want a re-render.
                    state["transcript"] = ""
                    state["pendingAction"] = None
                    lf_callback = get_langfuse_callback()
                    config = {
                        "callbacks": [lf_callback],
                        "metadata": {
                            "langfuse_session_id": sid,
                            "agent_id": sessions[sid].get("agent_id", "mortgage"),
                        },
                    }
                    _plugin = get_plugin(sessions[sid].get("agent_id", "mortgage"))
                    try:
                        res = await invoke_graph(_plugin, state, config)
                        sessions[sid]["state"] = res
                        await process_outbox(websocket, sid)
                    except Exception as e:
                        logger.error("Error re-rendering on device change: %s", e)
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
        if session_id in sessions:
            sess = sessions[session_id]
            # Cancel in-flight TTS so it doesn't try to send to the closed socket
            if sess.get("tts_task") and not sess["tts_task"].done():
                logger.info(f"Cancelling pending TTS task for {session_id}")
                sess["tts_task"].cancel()
            if sess.get("sonic"):
                logger.info(f"Ending Nova Sonic session for {session_id}")
                asyncio.create_task(sess["sonic"].end_session())
            del sessions[session_id]
            logger.info(f"Session {session_id} removed from registry")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


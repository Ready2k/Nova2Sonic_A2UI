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
from .agent.graph import app_graph, AgentState
from .nova_sonic import NovaSonicSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Barclays Mortgage Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

sessions: Dict[str, dict] = {}

def create_initial_state() -> AgentState:
    return {
        "mode": "voice",
        "transcript": "",
        "messages": [],
        "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25},
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "ui": {"surfaceId": "main", "state": "LOADING"},
        "errors": None,
        "pendingAction": None,
        "outbox": [],
        "existing_customer": None,
        "property_seen": None
    }

async def send_msg(websocket: WebSocket, session_id: str, msg_type: str, payload: dict = None):
    try:
        msg = WebSocketMessage(type=msg_type, sessionId=session_id, payload=payload)
        await websocket.send_text(msg.model_dump_json())
    except Exception as e:
        logger.error(f"Cannot send to ws: {e}")

async def run_tts_inline(websocket: WebSocket, session_id: str, text_to_speak: str):
    """Run Node TTS synchronously (awaited) so the WS stays open for the full audio stream."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "node", "nova_sonic_tts.mjs", text_to_speak,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(__file__))
        )
        while True:
            line = await proc.stdout.readline()
            if not line: break
            decoded = line.decode().strip()
            if decoded.startswith("AUDIO_CHUNK:"):
                await send_msg(websocket, session_id, "server.voice.audio", {"data": decoded.split("AUDIO_CHUNK:")[1]})
        await proc.wait()
    except Exception as e:
        logger.error(f"TTS fallback failed: {e}")
    finally:
        if session_id in sessions:
            sessions[session_id]["voice_playing"] = False
        await send_msg(websocket, session_id, "server.voice.stop", {})


async def process_outbox(websocket: WebSocket, sid: str):
    state = sessions[sid]["state"]
    outbox = state.get("outbox", [])
    sonic_active = sessions[sid].get("sonic") is not None
    
    # Pass 1: send ALL non-voice events immediately (a2ui.patch, transcript, etc.)
    for event in outbox:
        if event["type"] != "server.voice.say":
            logger.info(f"Emitting from outbox: {event['type']}")
            await send_msg(websocket, sid, event["type"], event.get("payload"))

    # Pass 2: handle voice.say last
    for event in outbox:
        if event["type"] == "server.voice.say":
            text_to_speak = event.get("payload", {}).get("text", "")
            logger.info(f"Emitting from outbox: server.voice.say -> '{text_to_speak[:60]}'")
            # Echo to chat transcript immediately
            await send_msg(websocket, sid, "server.transcript.final", {"text": text_to_speak, "role": "assistant"})
            
            if sonic_active:
                # Nova Sonic handles speaking — don't compete with a separate TTS process
                logger.info(f"Skipping TTS (Nova Sonic active)")
            elif not sessions[sid].get("voice_playing"):
                # No active voice session — fire TTS as background task (non-blocking)
                sessions[sid]["voice_playing"] = True
                asyncio.create_task(run_tts_inline(websocket, sid, text_to_speak))
    
    # Clear outbox
    state["outbox"] = []


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"sess_{id(websocket)}"
    
    sessions[session_id] = {
        "state": create_initial_state(),
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
        initial_res = await asyncio.to_thread(app_graph.invoke, sessions[session_id]["state"])
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
            
            state: AgentState = session_data["state"]

            if msg_type in ["client.audio.start", "client.audio.stop"]:
                logger.info(f"--- Received '{msg_type}' from {sid} ---")

            # Inline helpers for Nova Sonic callbacks
            async def handle_audio_chunk(chunk_b64):
                await send_msg(websocket, sid, "server.voice.audio", {"data": chunk_b64})

            async def handle_text_chunk(text, is_user=False):
                if is_user:
                    print(f"APPENDING USER TEXT: {text}", file=sys.stderr, flush=True)
                    session_data["user_transcripts"].append(text)
                else:
                    if "assist_buffer" not in session_data: session_data["assist_buffer"] = []
                    session_data["assist_buffer"].append(text)
                    await send_msg(websocket, sid, "server.transcript.final", {"text": text, "role": "assistant"}) 

            async def handle_finished():
                # ALWAYS read the latest state — never use the stale closure variable
                current_state = sessions[sid]["state"]
                
                assist_text = "".join(session_data.get("assist_buffer", [])).strip()
                if assist_text:
                    current_state["messages"].append({"role": "assistant", "text": assist_text})
                    session_data["assist_buffer"] = []

                full_transcript = " ".join(session_data["user_transcripts"]).strip()
                if not full_transcript:
                    return
                session_data["user_transcripts"] = []
                
                await send_msg(websocket, sid, "server.transcript.final", {"text": full_transcript, "role": "user"})
                
                current_state["transcript"] = full_transcript
                current_state["mode"] = "voice"
                current_state["messages"].append({"role": "user", "text": full_transcript})
                
                await send_msg(websocket, sid, "server.agent.thinking", {"state": "extracting_intent"})
                
                try:
                    res = await asyncio.to_thread(app_graph.invoke, current_state)
                    sessions[sid]["state"] = res
                    await process_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"Error in LangGraph matching (voice/finished): {e}")
                    traceback.print_exc()
                
            if msg_type == "client.audio.start":
                sonic = NovaSonicSession(
                    on_audio_chunk=handle_audio_chunk, 
                    on_text_chunk=lambda t: handle_text_chunk(t, False),
                    on_finished=handle_finished
                )
                
                original_process = sonic._process_responses
                async def patched_process():
                    try:
                        while sonic.is_active:
                            if not sonic.stream: break
                            output = await sonic.stream.await_output()
                            result = await output[1].receive()
                            if result is None or getattr(result, 'value', None) is None:
                                break

                            if getattr(result.value, 'bytes_', None):
                                response_data = result.value.bytes_.decode('utf-8')
                                try:
                                    json_data = json.loads(response_data)
                                except json.JSONDecodeError:
                                    continue

                                if 'event' in json_data:
                                    event = json_data['event']
                                    with open('bedrock_stream.log', 'a') as f:
                                        f.write(f"RAW EVENT: {json.dumps(event)}\n")
                                        if 'contentStart' in event:
                                            content_start = event['contentStart'] 
                                            sonic.role = content_start.get('role', '')
                                            if 'additionalModelFields' in content_start:
                                                af = json.loads(content_start['additionalModelFields'])
                                                if af.get('generationStage') == 'SPECULATIVE':
                                                    sonic.display_assistant_text = True
                                                else:
                                                    sonic.display_assistant_text = False
                                        elif 'textOutput' in event:
                                            text = event['textOutput']['content']    
                                            if sonic.role == "ASSISTANT" and sonic.display_assistant_text:
                                                if sonic.on_text_chunk:
                                                    await sonic.on_text_chunk(text)
                                            elif sonic.role == "USER":
                                                await handle_text_chunk(text, True)
                                        elif 'audioOutput' in event:
                                            audio_content = event['audioOutput']['content']
                                            if sonic.on_audio_chunk:
                                                await sonic.on_audio_chunk(audio_content)
                                        elif 'contentEnd' in event:
                                            if sonic.role == "USER":
                                                await handle_finished()
                                        elif 'promptEnd' in event:
                                            await handle_finished()
                                            
                            if result is None or getattr(result, 'value', None) is None:
                                await handle_finished()
                                break
                    except Exception as e:
                        import traceback
                        print(f"Nova Sonic Process Response Error: {e}", file=sys.stderr, flush=True)
                        traceback.print_exc(file=sys.stderr)
                        sonic.is_active = False

                sonic._process_responses = patched_process
                
                session_data["sonic"] = sonic
                session_data["user_transcripts"] = []
                
                try:
                    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
                    if not aws_access_key:
                        logger.warning("AWS Credentials not found. Nova Sonic will fail.")
                    
                    # Build a tight system prompt from the LATEST state (not stale closure)
                    current_intent = sessions[sid]["state"].get("intent", {})
                    category = current_intent.get("category", "a mortgage")
                    missing_prompt_parts = []
                    if current_intent.get("existingCustomer") is None:
                        missing_prompt_parts.append("whether they already bank with Barclays (answer: yes or no)")
                    elif current_intent.get("propertySeen") is None:
                        missing_prompt_parts.append("whether they have found a property yet (answer: yes or no)")
                    elif not current_intent.get("propertyValue"):
                        missing_prompt_parts.append("the property value in pounds (e.g. 400000)")
                    elif not current_intent.get("loanBalance"):
                        missing_prompt_parts.append("the loan amount they need in pounds")
                    elif not current_intent.get("fixYears"):
                        missing_prompt_parts.append("how many years they want to fix: 2, 3, 5, or 10")
                    
                    next_question = missing_prompt_parts[0] if missing_prompt_parts else "confirm they are happy to proceed"
                    
                    sys_prompt = (
                        f"You are a Barclays mortgage advisor. The customer is enquiring about: {category}. "
                        "RULES (follow exactly): "
                        "1. Respond with ONE sentence only. No lists, no explanations. "
                        "2. Do not greet or introduce yourself. "
                        f"3. Ask only for: {next_question}. "
                        "4. After asking, stop and wait for the answer."
                    )
                    await sonic.start_session(system_prompt=sys_prompt)
                    await sonic.start_audio_input()
                except Exception as e:
                    logger.error(f"Failed to start Nova Sonic session: {e}", exc_info=True)
                
            elif msg_type == "client.audio.chunk":
                if session_data["sonic"]:
                    b64 = payload.get("data")
                    if b64:
                        if "chunk_count" not in session_data:
                            session_data["chunk_count"] = 0
                        session_data["chunk_count"] += 1
                        if session_data["chunk_count"] % 50 == 0:
                            logger.info(f"--- Received {session_data['chunk_count']} audio chunks so far ---")
                        await session_data["sonic"].send_audio_chunk(b64)
                        
            elif msg_type == "client.audio.stop":
                if session_data["sonic"]:
                    await session_data["sonic"].end_audio_input()
                
            elif msg_type == "client.audio.interrupt":
                if session_data["sonic"]:
                    await session_data["sonic"].end_session()
                    session_data["sonic"] = None
                session_data["voice_playing"] = False
                await send_msg(websocket, sid, "server.voice.stop")
                
            elif msg_type == "client.text":
                transcript = payload.get("text", "")
                image_b64 = payload.get("image")
                
                state["transcript"] = transcript
                state["mode"] = "text"
                
                msg_obj = {"role": "user", "text": transcript}
                if image_b64:
                    if image_b64.startswith("data:"): image_b64 = image_b64.split(",", 1)[1]
                    msg_obj["image"] = image_b64
                state["messages"].append(msg_obj)
                
                await send_msg(websocket, sid, "server.transcript.final", {"text": transcript})
                await send_msg(websocket, sid, "server.agent.thinking", {"state": "rendering_ui"})
                
                try:
                    res = await asyncio.to_thread(app_graph.invoke, state)
                    sessions[sid]["state"] = res
                    await process_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"Error in LangGraph matching (text): {e}")
                    traceback.print_exc()
                    
            elif msg_type == "client.ui.action":
                action_id = payload.get("id")
                data = payload.get("data", {})
                
                logger.info(f"Received UI Action: {action_id} with data: {data}")
                
                try:
                    # Always use latest state (stale closure guard)
                    current_state = sessions[sid]["state"]
                    current_state["pendingAction"] = {"id": action_id, "data": data}
                    
                    res = await asyncio.to_thread(app_graph.invoke, current_state)
                    sessions[sid]["state"] = res
                    await process_outbox(websocket, sid)
                except Exception as e:
                    import traceback
                    logger.error(f"Error handling UI action '{action_id}': {e}")
                    traceback.print_exc()
                
    except WebSocketDisconnect:
        if session_id in sessions:
            if sessions[session_id].get("sonic"):
                asyncio.create_task(sessions[session_id]["sonic"].end_session())
            del sessions[session_id]

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
        "errors": None,
        "existing_customer": None,
        "property_seen": None,
        "last_event": None
    }

async def send_msg(websocket: WebSocket, session_id: str, msg_type: str, payload: dict = None):
    try:
        msg = WebSocketMessage(type=msg_type, sessionId=session_id, payload=payload)
        await websocket.send_text(msg.model_dump_json())
    except Exception as e:
        logger.error(f"Cannot send to ws: {e}")

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
        
        # Trigger initial UI rendering (Mortgage Options Dashboard)
        initial_res = await asyncio.to_thread(app_graph.invoke, sessions[session_id]["state"])
        sessions[session_id]["state"] = initial_res
        if initial_res.get("a2ui_payload"):
            await send_msg(websocket, session_id, "server.a2ui.patch", initial_res["a2ui_payload"])
        
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

            async def handle_audio_chunk(chunk_b64):
                await send_msg(websocket, sid, "server.voice.audio", {"data": chunk_b64})

            async def handle_text_chunk(text, is_user=False):
                if is_user:
                    print(f"APPENDING USER TEXT: {text}", file=sys.stderr, flush=True)
                    session_data["user_transcripts"].append(text)
                else:
                    # Echo Assistant's real-time transcript to the UI
                    # We store it temporarily so we can append the full message later
                    if "assist_buffer" not in session_data: session_data["assist_buffer"] = []
                    session_data["assist_buffer"].append(text)
                    await send_msg(websocket, sid, "server.transcript.final", {"text": text, "role": "assistant"}) 

            async def handle_finished():
                # Turn ending. Get assistant's full text if any
                assist_text = "".join(session_data.get("assist_buffer", [])).strip()
                if assist_text:
                    state["messages"].append({"role": "assistant", "text": assist_text})
                    session_data["assist_buffer"] = []

                full_transcript = " ".join(session_data["user_transcripts"]).strip()
                if not full_transcript:
                    # If we only have assistant text but no new user transcript, 
                    # we still might want to re-run graph to update UI state, 
                    # but usually unnecessary unless the graph depends on history.
                    return
                session_data["user_transcripts"] = []
                
                await send_msg(websocket, sid, "server.transcript.final", {"text": full_transcript, "role": "user"})
                
                state["transcript"] = full_transcript
                state["mode"] = "voice"
                state["messages"].append({"role": "user", "text": full_transcript})
                
                await send_msg(websocket, sid, "server.agent.thinking", {"state": "extracting_intent"})
                
                try:
                    res = await asyncio.to_thread(app_graph.invoke, state)
                    sessions[sid]["state"] = res
                    
                    a2ui_payload = res.get("a2ui_payload")
                    if a2ui_payload:
                        await send_msg(websocket, sid, "server.agent.thinking", {"state": "rendering_ui"})
                        await send_msg(websocket, sid, "server.a2ui.patch", a2ui_payload)
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
                
                # Replace the original process_responses to also emit on_user_transcript
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
                                            # Bedrock streams don't emit a promptEnd turn for the user. They emit contentEnd.
                                            # We must invoke the LangGraph A2UI update sequence when the user finishes speaking.
                                            if sonic.role == "USER":
                                                await handle_finished()

                                        elif 'promptEnd' in event:
                                            # Assistant turn finished. Update state with buffer.
                                            await handle_finished()
                                            
                            if result is None or getattr(result, 'value', None) is None:
                                # Catch stream closure just in case promptEnd isn't sent
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
                    
                    # Compute what's missing to guide the voice model
                    intent = state.get("intent", {})
                    missing = []
                    if intent.get("existingCustomer") is None: missing.append("if they bank with Barclays")
                    if intent.get("propertySeen") is None: missing.append("if they found a property")
                    if not intent.get("propertyValue"): missing.append("property value")
                    if not intent.get("loanBalance"): missing.append("loan balance")
                    if not intent.get("fixYears"): missing.append("fixed term years (2, 5, 10)")
                    
                    needed = ", ".join(missing) if missing else "nothing (all info gathered)"
                    
                    sys_prompt = (
                        "You are Barclays Mortgage Assistant. Your goal is to gather mortgage details conversationally. "
                        "IMPORTANT: The backend system currently needs: " + needed + ". "
                        "Prioritize asking for one of these missing items. Keep it very short (1-2 sentences). "
                        "If you already have enough info, say you've found some options and refer to the screen. "
                        "DO NOT say 'One moment' or 'I will retrieve details'. You do not have tools. Just talk."
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
                state["last_event"] = "text"
                
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
                    
                    # Directly emit the A2UI payload generated by the graph node
                    a2ui_payload = res.get("a2ui_payload")
                    if a2ui_payload:
                        await send_msg(websocket, sid, "server.agent.thinking", {"state": "rendering_ui"})
                        await send_msg(websocket, sid, "server.a2ui.patch", a2ui_payload)
                except Exception as e:
                    import traceback
                    logger.error(f"Error in LangGraph matching (text): {e}")
                    traceback.print_exc()
                    res = state # Fallback to current state

                intent = res.get("intent", {})
                missing = []
                # Baseline questions first
                if intent.get("existingCustomer") is None: missing.append("whether you already bank with Barclays")
                if intent.get("propertySeen") is None: missing.append("if you have already found a property")
                # Then financial/requirement questions
                if not intent.get("propertyValue"): missing.append("property value")
                if not intent.get("loanBalance"): missing.append("loan balance")
                if not intent.get("fixYears"): missing.append("fixed term (years)")
                
                if not missing:
                    ltv = res.get("ltv", 0)
                    msg = f"Based on a {ltv}% LTV, I’ve found two {intent.get('fixYears')}-year options."
                else:
                    await send_msg(websocket, sid, "server.agent.thinking", {"state": "generating_response"})
                    try:
                        from langchain_aws import ChatBedrockConverse
                        from langchain_core.messages import HumanMessage, SystemMessage
                        
                        model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
                        llm = ChatBedrockConverse(
                            model=model_id, 
                            region_name=os.getenv("AWS_REGION", "us-east-1")
                        )
                        lc_messages = [SystemMessage(content=(
                            f"You are Barclays Mortgage Assistant. The user is missing: {', '.join(missing)}. "
                            "Prioritize asking about banking status and property search before financial details. "
                            "Ask them conversationally for ONE of these missing details. Keep it very short, 1-2 sentences."
                        ))]
                        for m in state["messages"]:
                            lc_messages.append(HumanMessage(content=str(m.get("text", ""))))
                        
                        response = await asyncio.to_thread(llm.invoke, lc_messages)
                        msg_content = response.content
                        if isinstance(msg_content, list) and len(msg_content) > 0 and isinstance(msg_content[0], dict):
                            msg = str(msg_content[0].get("text", msg_content))
                        else:
                            msg = str(msg_content)
                    except Exception as e:
                        print(f"Text mode LLM fallback failed: {e}")
                        msg = f"Can you tell me your {missing[0]}?"
                        
                    # Echo the assistant's request so the text UI shows it
                    state["messages"].append({"role": "assistant", "text": msg})
                    await send_msg(websocket, sid, "server.transcript.final", {"text": msg, "role": "assistant"})
                    await send_msg(websocket, sid, "server.agent.thinking", {"state": "waiting_for_user"})
                
                if not session_data.get("voice_playing"):
                    session_data["voice_playing"] = True
                    async def run_tts(session_id, text_to_speak):
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
                        except: pass
                        finally:
                            if session_id in sessions:
                                sessions[session_id]["voice_playing"] = False
                                sessions[session_id]["tts_task"] = None
                            await send_msg(websocket, session_id, "server.voice.stop")
                    session_data["tts_task"] = asyncio.create_task(run_tts(sid, msg))
                    
            elif msg_type == "client.ui.action":
                action_id = payload.get("id")
                data = payload.get("data", {})
                
                logger.info(f"Received UI Action: {action_id} with data: {data}")
                state["last_event"] = "action"
                
                if action_id == "select_product":
                    state["selection"] = data
                elif action_id == "update_term":
                    state["intent"]["termYears"] = data.get("termYears", state["intent"]["termYears"])
                elif action_id == "confirm_application":
                    state["selection"]["confirmed"] = True
                elif action_id == "reset_flow":
                    # Simple reset logic
                    state["intent"] = {}
                    state["selection"] = {}
                    state["products"] = []
                
                # Re-invoke graph with updated state
                res = await asyncio.to_thread(app_graph.invoke, state)
                sessions[sid]["state"] = res
                
                # Emit update
                a2ui_payload = res.get("a2ui_payload")
                if a2ui_payload:
                    await send_msg(websocket, sid, "server.a2ui.patch", a2ui_payload)
                
                # Optional vocal confirmation
                if action_id == "select_product":
                    asyncio.create_task(run_tts(sid, "Great choice. I've prepared your summary. Would you like to proceed with the Agreement in Principle?"))
                
    except WebSocketDisconnect:
        if session_id in sessions:
            if sessions[session_id].get("sonic"):
                asyncio.create_task(sessions[session_id]["sonic"].end_session())
            del sessions[session_id]

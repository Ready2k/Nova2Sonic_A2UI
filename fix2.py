with open("/Users/jamescregeen/A2UI_S2S/server/app/main.py", "r") as f:
    text = f.read()

# wait, look at handle_text_chunk in main.py
# ```python
#             async def handle_text_chunk(text, is_user=False):
#                 if is_user:
#                     print(f"APPENDING USER TEXT: {text}", file=sys.stderr, flush=True)
#                     session_data["user_transcripts"].append(text)
#                 else: ...
# ```
# If `handle_text_chunk` is called continuously by Bedrock with speculative text outputs, it appends to `user_transcripts`.
# When user FINISHES, Bedrock sends `contentEnd` with `sonic.role == "USER"`.
# This triggers `handle_finished` ONCE.
# Wait! In `patched_process` (near line 230):
# ```python
#     elif 'contentEnd' in event:
#         if sonic.role == "USER":
#             await handle_finished()
#     elif 'promptEnd' in event:
#         await handle_finished()
# ```
# If the loop receives `contentEnd` for USER role, it calls `handle_finished()`.
# Then Bedrock sends `contentEnd` for ASSISTANT role (after it finishes talking). Which doesn't trigger handle_finished.
# BUT Bedrock also sends `promptEnd`! 
# Which calls `handle_finished()` again.
# This means `handle_finished()` is called once when the user finishes, and again when the assistant finishes.
# Let's verify `promptEnd` logic. It handles `promptEnd` from the assistant's turn. 
# Oh! Wait!
# When `handle_finished()` is called:
# The FIRST time (from USER `contentEnd`):
# full_transcript = "user said a thing"
# state["transcript"] = full_transcript
# it triggers graph! Graph sets `transcript: ""` and `outbox`.
# The SECOND time (from overall `promptEnd`):
# `user_transcripts` is empty. 
# `full_transcript` is empty.
# `handle_finished()` says `if not full_transcript: return`.
# Let's see if this is true.
# YES!
# ```python
#                 full_transcript = " ".join(session_data["user_transcripts"]).strip()
#                 if not full_transcript:
#                     return
# ```
# BUT WAIT.
# Does `handle_finished` do ANYTHING else before returning?
# ```python
#                 assist_text = "".join(session_data.get("assist_buffer", [])).strip()
#                 if assist_text:
#                     state["messages"].append({"role": "assistant", "text": assist_text})
#                     session_data["assist_buffer"] = []
# 
#                 full_transcript = " ".join(session_data["user_transcripts"]).strip()
#                 if not full_transcript:
#                     return
# ```
# Yes, it processes assistant buffer and then returns.

# Okay, why does the voice agent KEEP talking and not letting me answer or updating?
# Ah! I know!
# Let's look at `graph.py` inside `render_missing_inputs`:
# ```python
#     if missing:
#         msg = f"Can you tell me your {missing[0]}?"
#         # ... falls back to bedock Converse ...
#         outbox.append({"type": "server.transcript.final", "payload": {"text": msg, "role": "assistant"}})
#         outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
# ```
# It produces `{ "type": "server.voice.say", ... }`.
# WHICH goes to `main.py`'s `process_outbox`.
# What does `process_outbox` do with `voice.say`?
# ```python
#         # Intercept voice.say to drive Nova TTS fallback if needed
#         if event["type"] == "server.voice.say":
#             text_to_speak = event.get("payload", {}).get("text", "")
#             if not sessions[sid].get("voice_playing"):
#                 sessions[sid]["voice_playing"] = True
#                 async def run_tts(session_id, text_to_speak):
# ```
# OH MY GOD!
# Before, `NovaSonicSession` streamed the audio natively.
# Now I am pushing `server.voice.say` to be rendered by the Node TTS script fallback!!
# But wait, NovaSonicSession natively responds with audio if we just give it a prompt.
# In `main.py`:
# ```python
#                     # Instead of forcing the explicit text extraction loop inside the audio session (which can get stale if state evolves),
#                     # we let the fallback LLM inside graph.py emit exactly what to say to the UI and Audio stack.
#                     sys_prompt = (
#                         "You are Barclays Mortgage Assistant. Your goal is to gather mortgage details conversationally. "
#                         "DO NOT say 'One moment' or 'I will retrieve details'. You do not have tools. Just talk."
#                     )
#                     await sonic.start_session(system_prompt=sys_prompt)
# ```
# Yes! `NovaSonicSession` natively replies to user voice input via the constant stream! 
# We DO NOT NEED `server.voice.say` to be pushed for voice mode when using the native streaming Nova!
# Wait! If the user talks, `sonic` hears it, and `sonic` (Bedrock) directly generates the audio reply!
# BUT! In graph.py, `render_missing_inputs` ALSO generates a message via Converse API!
# AND sends it to TTS!
# So both Bedrock stream AND the Node TTS process are trying to speak!
# Let me fix this completely.

print("done")

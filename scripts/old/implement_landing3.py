with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "r") as f:
    text = f.read()

# Make missing logic prioritize category
text = text.replace(
'''    if intent.get("existingCustomer") is None: missing.append("whether you already bank with Barclays")''',
'''    if not intent.get("category"): missing.append("category")
    elif intent.get("existingCustomer") is None: missing.append("whether you already bank with Barclays")'''
)

# And if missing is category, we shouldn't ask a question with Bedrock, we just say "Hello! Please select a mortgage option to get started."
replacement_missing = '''    if missing:
        if missing[0] == "category":
            msg = "Hello! Please select a mortgage option on the screen, or just tell me what you're looking for to get started."
            if state.get("mode") != "voice" and not state.get("transcript"):
                outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
                outbox.append({"type": "server.transcript.final", "payload": {"text": msg, "role": "assistant"}})
                messages.append({"role": "assistant", "text": msg})
        else:
            msg = f"Can you tell me your {missing[0]}?"'''

text = text.replace(
'''    if missing:
        msg = f"Can you tell me your {missing[0]}?"''',
replacement_missing
)

with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "w") as f:
    f.write(text)

print("Updated missing logic")

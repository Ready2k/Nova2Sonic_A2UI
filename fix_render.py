with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "r") as f:
    text = f.read()

text = text.replace(
'''    outbox.append({"type": "server.voice.say", "payload": {"text": "Great choice. I've prepared your summary. You can review it on screen and confirm if you want to proceed."}})''',
'''    if state.get("mode") != "voice":
        outbox.append({"type": "server.voice.say", "payload": {"text": "Great choice. I've prepared your summary. You can review it on screen and confirm if you want to proceed."}})'''
)

with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "w") as f:
    f.write(text)

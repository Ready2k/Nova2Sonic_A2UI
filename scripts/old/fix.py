with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "r") as f:
    text = f.read()

text = text.replace('return {"outbox": outbox, "ui": ui_state, "messages": messages}', 'return {"outbox": outbox, "ui": ui_state, "messages": messages, "transcript": ""}')

with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "w") as f:
    f.write(text)

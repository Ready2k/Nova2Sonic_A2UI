import re

with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "r") as f:
    text = f.read()

# 1. Update start_router
text = text.replace(
'''def start_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"
    if state.get("transcript"):
        return "interpret_intent"
    return END''',
'''def start_router(state: AgentState):
    if state.get("pendingAction"):
        return "handle_ui_action"
    if state.get("transcript"):
        return "interpret_intent"
    
    intent = state.get("intent", {})
    if not intent.get("category") or intent.get("propertyValue") is None or intent.get("loanBalance") is None or intent.get("fixYears") is None:
        return "render_missing_inputs"
    return "call_mortgage_tools"'''
)

# 2. Add select_category to handle_ui_action
handle_replacement = '''    elif action_id == "reset_flow":
        return {
            "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25, "category": None},
            "selection": {},
            "products": [],
            "ltv": 0.0,
            "errors": None,
            "transcript": "",
            "existing_customer": None,
            "property_seen": None
        }
    elif action_id == "select_category":
        category = data.get("category")
        intent["category"] = category
        
        msg = f"Great, I can help you with {category}. Let's get started."
        outbox = list(state.get("outbox", []))
        messages = list(state.get("messages", []))
        
        if state.get("mode") != "voice":
            outbox.append({"type": "server.voice.say", "payload": {"text": msg}})
        messages.append({"role": "assistant", "text": msg})
        
        return {"intent": intent, "outbox": outbox, "messages": messages}'''

text = text.replace(
'''    elif action_id == "reset_flow":
        return {
            "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25},
            "selection": {},
            "products": [],
            "ltv": 0.0,
            "errors": None,
            "transcript": "",
            "existing_customer": None,
            "property_seen": None
        }''',
handle_replacement
)

# 3. Add to ui_action_router
text = text.replace(
'''    elif action_id == "reset_flow":
        return "render_missing_inputs"''',
'''    elif action_id == "reset_flow":
        return "render_missing_inputs"
    elif action_id == "select_category":
        return "render_missing_inputs"'''
)

with open("/Users/jamescregeen/A2UI_S2S/server/app/agent/graph.py", "w") as f:
    f.write(text)

print("Updates to routing and actions applied.")

import os
import sys
import json
import asyncio
from dotenv import load_dotenv

sys.path.append(os.path.join(os.path.dirname(__file__), "server"))

from server.app.agent.graph import app_graph, AgentState

load_dotenv()

async def run_test_case(name, state_input, expected_keys=None):
    print(f"\n--- Running Test Case: {name} ---")
    res = await asyncio.to_thread(app_graph.invoke, state_input)
    
    print("Agent Output State Keys:", list(res.keys()))
    
    intent = res.get("intent", {})
    print(f"Extracted Intent: {intent}")
    print(f"Computed LTV: {res.get('ltv')}%")
    
    products = res.get("products", [])
    print(f"Products Found: {len(products)}")
    for p in products:
        print(f"  - {p['id']}: {p['name']} ({p['rate']}%)")
    
    outbox = res.get("outbox", [])
    a2ui_patches = [evt for evt in outbox if evt["type"] == "server.a2ui.patch"]
    voice_says = [evt for evt in outbox if evt["type"] == "server.voice.say"]
    
    if a2ui_patches:
        print(f"A2UI Payload emitted ({len(a2ui_patches)} patch(es)). Target state: {res.get('ui', {}).get('state')}")
    else:
        print("WARNING: No A2UI Payload generated.")
        
    for vs in voice_says:
        print(f"Voice Say Emitted: {vs['payload']['text']}")
        
    # clear outbox between runs to emulate main.py behavior
    res["outbox"] = []
    return res

async def main():
    model_id = os.getenv("TEST_MODEL_ID", "amazon.nova-lite-v1:0")
    print(f"Using Model: {model_id}")
    
    state1 = {
        "mode": "text",
        "transcript": "My house is worth 400000",
        "messages": [{"role": "user", "text": "My house is worth 400000"}],
        "intent": {"termYears": 25, "propertyValue": None, "loanBalance": None, "fixYears": None},
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "ui": {"surfaceId": "main", "state": "LOADING"},
        "pendingAction": None,
        "outbox": [],
        "errors": None,
        "existing_customer": None,
        "property_seen": None
    }
    res1 = await run_test_case("Partial Info (Property Value Only, asks for Loan Balance)", state1)
    
    state2 = state1.copy()
    state2["transcript"] = "My house is worth 400k and I owe 250k. Show me 5-year fixes."
    state2["messages"] = [{"role": "user", "text": state2["transcript"]}]
    state2["intent"] = {"termYears": 25, "existingCustomer": True, "propertySeen": True}
    
    res2 = await run_test_case("Full Info (PVC, LB, FixYears)", state2)
    
    state3 = res2.copy()
    state3["pendingAction"] = {"id": "select_product", "data": {"productId": "prod_standard_fix"}}
    res3 = await run_test_case("UI Action (Select Product -> Summary)", state3)
    
    state4 = res3.copy()
    state4["pendingAction"] = {"id": "confirm_application", "data": {}}
    res4 = await run_test_case("UI Action (Confirm Application)", state4)
    
    state5 = res4.copy()
    state5["pendingAction"] = {"id": "reset_flow", "data": {}}
    res5 = await run_test_case("UI Action (Reset Flow)", state5)

if __name__ == "__main__":
    asyncio.run(main())

    state_landing = {
        "mode": "text",
        "transcript": "",
        "messages": [],
        "intent": {"propertyValue": None, "loanBalance": None, "fixYears": None, "termYears": 25, "category": None},
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "ui": {"surfaceId": "main", "state": "LOADING"},
        "pendingAction": None,
        "outbox": [],
        "errors": None,
        "existing_customer": None,
        "property_seen": None
    }
    await run_test_case("Initial Landing", state_landing)

    state_selected = state_landing.copy()
    state_selected["pendingAction"] = {"id": "opt_ftb", "data": {"action": "select_category", "category": "First-time buyer"}}
    await run_test_case("Select Category", state_selected)

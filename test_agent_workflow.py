import os
import sys
import json
import asyncio
from dotenv import load_dotenv

# Add server directory to path to import app.agent
sys.path.append(os.path.join(os.path.dirname(__file__), "server"))

from server.app.agent.graph import app_graph, AgentState

load_dotenv()

async def run_test_case(name, state_input, expected_keys=None):
    print(f"\n--- Running Test Case: {name} ---")
    # Wrap synchronous graph.invoke in to_thread just like in main.py
    res = await asyncio.to_thread(app_graph.invoke, state_input)
    
    print("Agent Output State Keys:", list(res.keys()))
    
    intent = res.get("intent", {})
    print(f"Extracted Intent: {intent}")
    print(f"Computed LTV: {res.get('ltv')}%")
    
    products = res.get("products", [])
    print(f"Products Found: {len(products)}")
    for p in products:
        print(f"  - {p['id']}: {p['name']} ({p['rate']}%)")
    
    a2ui = res.get("a2ui_payload")
    if a2ui:
        print("A2UI Payload generated successfully.")
    else:
        print("WARNING: No A2UI Payload generated.")
        
    return res

async def main():
    model_id = os.getenv("TEST_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    print(f"Using Model: {model_id}")
    
    # Update environment for the runtime
    # Note: main.py uses hardcoded Haiku in some places, 
    # but the graph.py interpret_intent uses the model ID if we were to parameterize it.
    # Looking at graph.py: interpret_intent has Haiku hardcoded. 
    # I should probably update graph.py to use an environment variable for the model ID.
    
    # Test Case 1: Partial Info
    state1 = {
        "transcript": "My house is worth 400000",
        "messages": [{"role": "user", "text": "My house is worth 400000"}],
        "intent": {"termYears": 25},
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "last_event": "text"
    }
    await run_test_case("Partial Info (Property Value Only)", state1)
    
    # Test Case 2: Full Info
    state2 = {
        "transcript": "My house is worth 400k and I owe 250k. Show me 5-year fixes.",
        "messages": [{"role": "user", "text": "My house is worth 400k and I owe 250k. Show me 5-year fixes."}],
        "intent": {"termYears": 25},
        "ltv": 0.0,
        "products": [],
        "selection": {},
        "last_event": "text"
    }
    res2 = await run_test_case("Full Info (PVC, LB, FixYears)", state2)
    
    # Test Case 3: UI Action (Select Product)
    state3 = res2.copy()
    state3["selection"] = {"productId": "prod_standard_fix"}
    state3["last_event"] = "action"
    await run_test_case("UI Action (Select Product)", state3)

if __name__ == "__main__":
    asyncio.run(main())

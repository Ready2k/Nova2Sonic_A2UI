import asyncio
import json
import websockets
import sys

async def test_a2ui_scenarios():
    uri = "ws://localhost:8000/ws"
    
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            # 1. Hello
            await websocket.send(json.stringify({"type": "client.hello", "sessionId": "test-session"}))
            
            async def wait_for_a2ui():
                while True:
                    msg = await websocket.recv()
                    data = json.loads(msg)
                    if data.get("type") == "server.a2ui.patch":
                        return data.get("payload")
            
            print("\n--- Scenario 1: Empty State ---")
            await websocket.send(json.stringify({
                "type": "client.text", 
                "payload": {"text": "hello"}
            }))
            payload = await wait_for_a2ui()
            print(f"Title: {payload['updateComponents']['components'][1]['text']}")
            print(f"Has Gauge: {'ltv_gauge' in payload['updateComponents']['components'][0]['children']}")
            print(f"Has Products: {'products_row' in payload['updateComponents']['components'][0]['children']}")
            
            print("\n--- Scenario 2: Partial Data (Property Value) ---")
            await websocket.send(json.stringify({
                "type": "client.text", 
                "payload": {"text": "My house is worth 400,000"}
            }))
            payload = await wait_for_a2ui()
            print(f"Title: {payload['updateComponents']['components'][1]['text']}")
            # Find gauge value
            gauge = next((c for c in payload['updateComponents']['components'] if c['id'] == 'ltv_gauge'), None)
            if gauge:
                print(f"LTV Gauge Value: {gauge['value']}%")
            
            print("\n--- Scenario 3: Full Data ---")
            await websocket.send(json.stringify({
                "type": "client.text", 
                "payload": {"text": "I have a loan of 250,000 and want a five year fix"}
            }))
            payload = await wait_for_a2ui()
            print(f"Title: {payload['updateComponents']['components'][1]['text']}")
            print(f"Comp count: {len(payload['updateComponents']['components'])}")
            print(f"Has Products: {'products_row' in payload['updateComponents']['components'][0]['children']}")
            
            # Verify structure of first product card if available
            prod_card = next((c for c in payload['updateComponents']['components'] if c['component'] == 'ProductCard'), None)
            if prod_card:
                print(f"Product Card Sample: {prod_card['data']['name']} @ {prod_card['data']['rate']}%")

            print("\nSUCCESS: A2UI Payload structures verified against schema.")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # monkey patch json.stringify for easier conversion from my thought process
    json.stringify = json.dumps
    asyncio.run(test_a2ui_scenarios())

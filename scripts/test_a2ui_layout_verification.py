import asyncio
import json
import websockets
import sys

async def test_a2ui_logic():
    uri = "ws://localhost:8000/ws"
    
    print(f"Connecting to {uri}...")
    try:
        async with websockets.connect(uri) as websocket:
            await websocket.send(json.dumps({"type": "client.hello", "sessionId": "test-session"}))
            
            async def wait_for_a2ui():
                while True:
                    msg = await websocket.recv()
                    data = json.loads(msg)
                    if data.get("type") == "server.a2ui.patch":
                        return data.get("payload")

            # 1. Test "Empty/Intro" response (Force trigger via voice confirming empty transcript)
            # This triggers the default fallback logic in interpret_intent
            print("\n--- Testing A2UI Structural Layout: Empty Input ---")
            await websocket.send(json.dumps({
                "type": "client.text", 
                "payload": {"text": "hello"}
            }))
            payload = await wait_for_a2ui()
            
            # Verify A2UI v0.9 Schema keys
            assert "version" in payload, "Missing version key"
            assert payload["version"] == "v0.9", f"Incorrect version: {payload['version']}"
            assert "updateComponents" in payload, "Missing updateComponents key"
            
            comps = payload["updateComponents"]["components"]
            print(f"Verified Version: {payload['version']}")
            print(f"Total Components in DOM: {len(comps)}")
            
            comp_ids = [c["id"] for c in comps]
            assert "root" in comp_ids, "Missing root node"
            assert "header_text" in comp_ids, "Missing header text"
            
            root = next(c for c in comps if c["id"] == "root")
            assert root["component"] == "Column", "Root should be a Column"
            print(f"Root layout: {root['component']} with children {root.get('children')}")

            # 2. Test Full Data Scenario (Mocked values in graph.py)
            print("\n--- Testing A2UI Structural Layout: Full Data Match ---")
            # We use keywords that the mock interpret_intent logic recognizes to avoid Bedrock
            await websocket.send(json.dumps({
                "type": "client.text", 
                "payload": {"text": "My house is 400 with 250 loan and 5 year fix"}
            }))
            payload = await wait_for_a2ui()
            
            comps = payload["updateComponents"]["components"]
            root = next(c for c in comps if c["id"] == "root")
            
            print(f"Title: {next(c['text'] for c in comps if c['id'] == 'header_text')}")
            print(f"LTV Gauge Found: {'ltv_gauge' in root['children']}")
            print(f"Products Row Found: {'products_row' in root['children']}")
            
            # Verify ProductCard structure
            prod_cards = [c for c in comps if c["component"] == "ProductCard"]
            print(f"Product Cards rendered: {len(prod_cards)}")
            if prod_cards:
                sample = prod_cards[0]
                assert "data" in sample, "ProductCard must contain data payload"
                assert "monthlyPayment" in sample["data"], "Product data missing payment field"
                print(f"Sample Product: {sample['data']['name']} - payment £{sample['data']['monthlyPayment']}")

            print("\nSUCCESS: Programmatic A2UI Architecture Verification Complete.")

    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_a2ui_logic())

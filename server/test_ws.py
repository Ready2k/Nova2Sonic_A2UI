import asyncio
import websockets
import json
import base64

async def test():
    async with websockets.connect("ws://localhost:8000/ws") as websocket:
        # Wait for ready
        ready = await websocket.recv()
        print("Received:", ready)
        
        # Send audio start
        await websocket.send(json.dumps({
            "type": "client.audio.start",
            "sessionId": "init-123",
            "payload": {}
        }))
        print("Sent audio start")
        
        # Send some audio chunks (silence)
        # 128 samples of 16-bit silence = 256 bytes
        silence = bytes(256)
        b64 = base64.b64encode(silence).decode('utf-8')
        
        for i in range(10):
            await websocket.send(json.dumps({
                "type": "client.audio.chunk",
                "sessionId": "init-123",
                "payload": {"data": b64}
            }))
            await asyncio.sleep(0.01)
        
        print("Sent 10 silence chunks, sending audio stop...", flush=True)
        await websocket.send(json.dumps({
            "type": "client.audio.stop",
            "sessionId": "init-123",
            "payload": {}
        }))
        print("Sent audio stop, waiting for model response...", flush=True)

        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            print(f"Received from model: {response}", flush=True)
        except asyncio.TimeoutError:
            print("Timeout waiting for model response.", flush=True)

asyncio.run(test())

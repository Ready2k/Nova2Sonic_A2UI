import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

def test_nova_sonic():
    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    
    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"text": "Hello, how are you today?"}]
            }
        ],
        "system": [{"text": "You are a helpful assistant."}],
        "inferenceConfig": {"maxTokens": 1024, "temperature": 0.7, "topP": 0.9},
        "additionalModelRequestFields": {"audio": {"format": "mp3"}}
    }
    
    try:
        response = client.invoke_model_with_response_stream(
            modelId="amazon.nova-2-sonic-v1:0",
            body=json.dumps(body)
        )
        for event in response.get("body"):
            chunk = event.get("chunk")
            if chunk:
                try:
                    data = json.loads(chunk.get("bytes").decode())
                    print("Received chunk keys:", list(data.keys()))
                except Exception as e:
                    pass
    except Exception as e:
        print("Error invoking model with response stream:", e)
        
if __name__ == "__main__":
    test_nova_sonic()

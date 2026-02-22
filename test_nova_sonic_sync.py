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
        "additionalModelRequestFields": {
            "audio": {"format": "mp3"}
        }
    }
    
    try:
        response = client.invoke_model(
            modelId="amazon.nova-2-sonic-v1:0",
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json"
        )
        response_body = json.loads(response.get("body").read())
        print("Response received:", response_body)
    except Exception as e:
        print("Error invoking model:", e)
        
if __name__ == "__main__":
    test_nova_sonic()

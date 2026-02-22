import os
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

def test_bedrock():
    try:
        model_id = os.getenv("AGENT_MODEL_ID", "amazon.nova-lite-v1:0")
        region = os.getenv("AWS_REGION", "us-east-1")
        llm = ChatBedrockConverse(model=model_id, region_name=region)
        res = llm.invoke([HumanMessage(content="Hello, can you hear me?")])
        print(f"Bedrock Response: {res.content}")
        return True
    except Exception as e:
        print(f"Bedrock Error: {e}")
        return False

if __name__ == "__main__":
    test_bedrock()

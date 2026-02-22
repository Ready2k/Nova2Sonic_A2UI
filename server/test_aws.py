import boto3
import os
from dotenv import load_dotenv

load_dotenv()

try:
    client = boto3.client('sts', region_name=os.getenv("AWS_REGION", "us-east-1"))
    response = client.get_caller_identity()
    print(f"Successfully connected as {response['Arn']}")
except Exception as e:
    print(f"AWS Error: {e}")

import os
import asyncio
import base64
import json
import logging
import sys
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

class NovaSonicSession:
    def __init__(self, on_audio_chunk, on_text_chunk, on_finished):
        self.model_id = 'amazon.nova-2-sonic-v1:0'
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.client = boto3.client("bedrock-runtime", region_name=self.region)
        
        self.on_audio_chunk = on_audio_chunk
        self.on_text_chunk = on_text_chunk
        self.on_finished = on_finished
        
        self.is_active = False
        self.audio_buffer = []  # To store base64 chunks
        self.system_prompt = ""

    async def start_session(self, system_prompt: str = None):
        logger.info("Nova Sonic: Starting session (buffered)")
        self.system_prompt = system_prompt or ""
        self.audio_buffer = []
        self.is_active = True

    async def start_audio_input(self):
        logger.info("Nova Sonic: Ready for audio input")
        self.audio_buffer = []

    async def send_audio_chunk(self, base64_audio: str):
        if not self.is_active:
            return
        # Just buffer it for now since we're using turn-based invoke_model_with_response_stream
        self.audio_buffer.append(base64_audio)

    async def end_audio_input(self):
        if not self.is_active:
            return
        
        logger.info(f"Nova Sonic: Audio input ended, processing {len(self.audio_buffer)} chunks")
        full_audio_b64 = "".join(self.audio_buffer)
        self.audio_buffer = []
        
        try:
            # Prepare body for Nova 2 Sonic
            # We use the 'converse' style or 'invoke_model' style. 
            # For Sonic, a common pattern for voice-to-text is providing the audio in a content block.
            body = {
                "schemaVersion": "messages-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "audio": {
                                    "format": "pcm",
                                    "value": full_audio_b64
                                }
                            }
                        ]
                    }
                ],
                "system": [{"text": self.system_prompt}] if self.system_prompt else None,
                "inferenceConfig": {
                    "maxNewTokens": 512,
                    "temperature": 0.1
                }
            }
            
            # Remove None system if empty
            if not body["system"]: del body["system"]

            response = self.client.invoke_model_with_response_stream(
                modelId=self.model_id,
                body=json.dumps(body)
            )

            # Process the streaming response
            current_role = "assistant"
            for event in response.get('body'):
                chunk_bytes = event.get('chunk').get('bytes')
                chunk_str = chunk_bytes.decode()
                chunk = json.loads(chunk_str)
                
                # Support both 'messages-v1' and native schemas
                if 'messageStart' in chunk:
                    current_role = chunk['messageStart'].get('role', 'assistant')
                    logger.info(f"Nova Sonic: Message Started (role={current_role})")
                
                # Capture role from contentStart if present
                if 'contentStart' in chunk:
                    current_role = chunk['contentStart'].get('role', current_role)

                # messages-v1 delta
                if 'contentBlockDelta' in chunk:
                    delta = chunk['contentBlockDelta']['delta']
                    if 'text' in delta:
                        text = delta['text']
                        if self.on_text_chunk:
                            is_user = (current_role == "user")
                            await self.on_text_chunk(text, is_user)
                
                # Native Nova schema event
                elif 'textOutput' in chunk:
                    text_out = chunk['textOutput']
                    text = text_out.get('content', '')
                    role = text_out.get('role', 'assistant')
                    if self.on_text_chunk:
                        is_user = (role.upper() == "USER")
                        await self.on_text_chunk(text, is_user)
                
                elif 'messageStop' in chunk or 'completionEnd' in chunk:
                    break

            if self.on_finished:
                await self.on_finished()

        except ClientError as e:
            logger.error(f"Bedrock API error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in transcription: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_active = False

    async def end_session(self):
        self.is_active = False
        self.audio_buffer = []

    async def _process_responses(self):
        # This was used for real-time bidirectional. 
        # In buffered mode, end_audio_input handles the full cycle.
        pass

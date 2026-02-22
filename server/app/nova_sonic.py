import os
import asyncio
import base64
import json
import uuid
from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient, InvokeModelWithBidirectionalStreamOperationInput
from aws_sdk_bedrock_runtime.models import InvokeModelWithBidirectionalStreamInputChunk, BidirectionalInputPayloadPart
from aws_sdk_bedrock_runtime.config import Config
from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver
import logging
import sys

logger = logging.getLogger(__name__)

class NovaSonicSession:
    def __init__(self, on_audio_chunk, on_text_chunk, on_finished):
        self.model_id = 'amazon.nova-2-sonic-v1:0'
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.client = None
        self.stream = None
        self.response_task = None
        self.is_active = False
        
        self.prompt_name = str(uuid.uuid4())
        self.content_name = str(uuid.uuid4())
        self.audio_content_name = str(uuid.uuid4())
        
        self.on_audio_chunk = on_audio_chunk
        self.on_text_chunk = on_text_chunk
        self.on_finished = on_finished
        
        self.role = None
        self.display_assistant_text = False

    def _initialize_client(self):
        from smithy_core.aio.interfaces.identity import IdentityResolver
        from smithy_aws_core.identity.components import AWSCredentialsIdentity, AWSIdentityProperties

        class Boto3CredentialsResolver(IdentityResolver[AWSCredentialsIdentity, AWSIdentityProperties]):
            async def get_identity(self, *, properties: AWSIdentityProperties) -> AWSCredentialsIdentity:
                import boto3
                session = boto3.Session()
                creds = session.get_credentials()
                if creds:
                    frozen = creds.get_frozen_credentials()
                    return AWSCredentialsIdentity(
                        access_key_id=frozen.access_key,
                        secret_access_key=frozen.secret_key,
                        session_token=frozen.token,
                    )
                raise Exception("Could not find AWS credentials via boto3")

        config = Config(
            endpoint_uri=f"https://bedrock-runtime.{self.region}.amazonaws.com",
            region=self.region,
            aws_credentials_identity_resolver=Boto3CredentialsResolver(),
        )
        self.client = BedrockRuntimeClient(config=config)

    async def send_event(self, event_json):
        if not self.stream:
            return
        event = InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_json.encode('utf-8'))
        )
        await self.stream.input_stream.send(event)

    async def start_session(self, system_prompt: str = None):
        try:
            print("Nova Sonic start_session: INITIALIZING SESSION", file=sys.stderr, flush=True)
            print("Nova Sonic start_session: initializing client", file=sys.stderr, flush=True)
            if not self.client:
                self._initialize_client()

            print("Nova Sonic start_session: invoking stream", file=sys.stderr, flush=True)
            self.stream = await self.client.invoke_model_with_bidirectional_stream(
                InvokeModelWithBidirectionalStreamOperationInput(model_id=self.model_id)
            )
            self.is_active = True
            print("Nova Sonic start_session: sending sessionStart", file=sys.stderr, flush=True)

            # Session start
            session_start = '''
            {
              "event": {
                "sessionStart": {
                  "inferenceConfiguration": {
                    "maxTokens": 1024,
                    "topP": 0.9,
                    "temperature": 0.7
                  }
                }
              }
            }
            '''
            await self.send_event(session_start)

            print("Nova Sonic start_session: sending promptStart", file=sys.stderr, flush=True)
            # Prompt start
            prompt_start = f'''
            {{
              "event": {{
                "promptStart": {{
                  "promptName": "{self.prompt_name}",
                  "textOutputConfiguration": {{
                    "mediaType": "text/plain"
                  }},
                  "audioOutputConfiguration": {{
                    "mediaType": "audio/lpcm",
                    "sampleRateHertz": 24000,
                    "sampleSizeBits": 16,
                    "channelCount": 1,
                    "voiceId": "matthew",
                    "encoding": "base64",
                    "audioType": "SPEECH"
                  }}
                }}
              }}
            }}
            '''
            await self.send_event(prompt_start)

            # Send system prompt if provided
            if system_prompt:
                print("Nova Sonic start_session: sending textContent", file=sys.stderr, flush=True)
                # ... skipping system prompt logs for brevity ...
                text_content_start = f'''
                {{
                    "event": {{
                        "contentStart": {{
                            "promptName": "{self.prompt_name}",
                            "contentName": "{self.content_name}",
                            "type": "TEXT",
                            "interactive": false,
                            "role": "SYSTEM",
                            "textInputConfiguration": {{
                                "mediaType": "text/plain"
                            }}
                        }}
                    }}
                }}
                '''
                await self.send_event(text_content_start)

                text_input = f'''
                {{
                    "event": {{
                        "textInput": {{
                            "promptName": "{self.prompt_name}",
                            "contentName": "{self.content_name}",
                            "content": "{system_prompt}"
                        }}
                    }}
                }}
                '''
                await self.send_event(text_input)

                text_content_end = f'''
                {{
                    "event": {{
                        "contentEnd": {{
                            "promptName": "{self.prompt_name}",
                            "contentName": "{self.content_name}"
                        }}
                    }}
                }}
                '''
                await self.send_event(text_content_end)

            print("Nova Sonic start_session: creating background response task", file=sys.stderr, flush=True)
            self.response_task = asyncio.create_task(self._process_responses())
        except Exception as e:
            import traceback
            print(f"Nova Sonic start_session ERROR: {e}", file=sys.stderr, flush=True)
            traceback.print_exc(file=sys.stderr)
            raise

    async def start_audio_input(self):
        audio_content_start = f'''
        {{
            "event": {{
                "contentStart": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}",
                    "type": "AUDIO",
                    "interactive": true,
                    "role": "USER",
                    "audioInputConfiguration": {{
                        "mediaType": "audio/lpcm",
                        "sampleRateHertz": 16000,
                        "sampleSizeBits": 16,
                        "channelCount": 1,
                        "audioType": "SPEECH",
                        "encoding": "base64"
                    }}
                }}
            }}
        }}
        '''
        await self.send_event(audio_content_start)

    async def send_audio_chunk(self, base64_audio: str):
        if not self.is_active:
            return

        audio_event = f'''
        {{
            "event": {{
                "audioInput": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}",
                    "content": "{base64_audio}"
                }}
            }}
        }}
        '''
        await self.send_event(audio_event)

    async def end_audio_input(self):
        if not self.is_active:
            return
            
        audio_content_end = f'''
        {{
            "event": {{
                "contentEnd": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}"
                }}
            }}
        }}
        '''
        await self.send_event(audio_content_end)
        
        prompt_end = f'''
        {{
            "event": {{
                "promptEnd": {{
                    "promptName": "{self.prompt_name}"
                }}
            }}
        }}
        '''
        await self.send_event(prompt_end)

    async def end_session(self):
        if not self.is_active:
            return

        # Attempt to end any open content block before promptEnd
        audio_content_end = f'''
        {{
            "event": {{
                "contentEnd": {{
                    "promptName": "{self.prompt_name}",
                    "contentName": "{self.audio_content_name}"
                }}
            }}
        }}
        '''
        try:
            await self.send_event(audio_content_end)
        except:
            pass

        prompt_end = f'''
        {{
            "event": {{
                "promptEnd": {{
                    "promptName": "{self.prompt_name}"
                }}
            }}
        }}
        '''
        try:
            await self.send_event(prompt_end)
        except:
            pass

        session_end = '''
        {
            "event": {
                "sessionEnd": {}
            }
        }
        '''
        try:
            await self.send_event(session_end)
            if self.stream and self.stream.input_stream:
                await self.stream.input_stream.close()
        except:
            pass
        self.is_active = False

    async def _process_responses(self):
        try:
            while self.is_active:
                if not self.stream: break
                import sys
                print("Nova Sonic _process_responses: waiting for output...", file=sys.stderr, flush=True)
                output = await self.stream.await_output()
                print("Nova Sonic _process_responses: got output, waiting for receive...", file=sys.stderr, flush=True)
                result = await output[1].receive()
                print("Nova Sonic _process_responses: received result!", file=sys.stderr, flush=True)

                if result.value and result.value.bytes_:
                    response_data = result.value.bytes_.decode('utf-8')
                    try:
                        json_data = json.loads(response_data)
                    except json.JSONDecodeError:
                        continue

                    if 'event' in json_data:
                        event = json_data['event']
                        if 'contentStart' in event:
                            content_start = event['contentStart'] 
                            self.role = content_start.get('role', '')
                            if 'additionalModelFields' in content_start:
                                af = json.loads(content_start['additionalModelFields'])
                                if af.get('generationStage') == 'SPECULATIVE':
                                    self.display_assistant_text = True
                                else:
                                    self.display_assistant_text = False

                        elif 'textOutput' in event:
                            text = event['textOutput']['content']    
                            if self.role == "ASSISTANT" and self.display_assistant_text:
                                if self.on_text_chunk:
                                    await self.on_text_chunk(text)
                                    
                        elif 'audioOutput' in event:
                            audio_content = event['audioOutput']['content']
                            if self.on_audio_chunk:
                                await self.on_audio_chunk(audio_content)

                        elif 'promptEnd' in event:
                            if self.on_finished:
                                await self.on_finished()
        except Exception as e:
            import traceback
            print(f"Nova Sonic Process Response Error: {e}")
            traceback.print_exc()
            self.is_active = False

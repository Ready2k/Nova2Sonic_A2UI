import {
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamCommand
} from '@aws-sdk/client-bedrock-runtime';
import dotenv from 'dotenv';
import * as path from 'path';
import { fileURLToPath } from 'url';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });

const textToSpeak = process.argv[2];
if (!textToSpeak) {
    console.error("Usage: node nova_sonic_tts.js <text>");
    process.exit(1);
}

const voiceId = process.argv[3] || 'matthew';

const client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1'
});

async function main() {
    const promptName = `tts-prompt-${Date.now()}`;
    const textContentName = `text-${Date.now()}`;
    const audioContentName = `audio-${Date.now()}`;
    const systemContentName = `system-${Date.now()}`;

    let canFinish = false;
    const finishSignal = () => { canFinish = true; };

    async function* inputStream() {
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        sessionStart: {
                            inferenceConfiguration: {
                                maxTokens: 2048,
                                topP: 0.9,
                                temperature: 0.7
                            },
                            turnDetectionConfiguration: {
                                endpointingSensitivity: "HIGH"
                            }
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        promptStart: {
                            promptName,
                            textOutputConfiguration: {
                                mediaType: "text/plain"
                            },
                            audioOutputConfiguration: {
                                mediaType: "audio/lpcm",
                                sampleRateHertz: 24000,
                                sampleSizeBits: 16,
                                channelCount: 1,
                                voiceId: voiceId,
                                encoding: "base64",
                                audioType: "SPEECH"
                            }
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: systemContentName,
                            type: "TEXT",
                            interactive: false,
                            role: "SYSTEM",
                            textInputConfiguration: {
                                mediaType: "text/plain"
                            }
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        textInput: {
                            promptName,
                            contentName: systemContentName,
                            content: "Your only job is to speak the user's text EXACTLY as written, word-for-word, without adding any commentary, questions, or additional words."
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentEnd: {
                            promptName,
                            contentName: systemContentName
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: textContentName,
                            type: "TEXT",
                            interactive: true,
                            role: "USER",
                            textInputConfiguration: {
                                mediaType: "text/plain"
                            }
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        textInput: {
                            promptName,
                            contentName: textContentName,
                            content: textToSpeak
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentEnd: {
                            promptName,
                            contentName: textContentName
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: audioContentName,
                            type: "AUDIO",
                            interactive: true,
                            role: "USER",
                            audioInputConfiguration: {
                                mediaType: "audio/lpcm",
                                sampleRateHertz: 16000,
                                sampleSizeBits: 16,
                                channelCount: 1,
                                audioType: "SPEECH",
                                encoding: "base64"
                            }
                        }
                    }
                }))
            }
        };

        const SILENCE_DURATION_MS = 100;
        const SAMPLE_RATE = 16000;
        const BYTES_PER_SAMPLE = 2;
        const SILENCE_BYTES = (SAMPLE_RATE * SILENCE_DURATION_MS / 1000) * BYTES_PER_SAMPLE;
        const silenceFrame = Buffer.alloc(SILENCE_BYTES, 0);

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        audioInput: {
                            promptName,
                            contentName: audioContentName,
                            content: silenceFrame.toString('base64')
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentEnd: {
                            promptName,
                            contentName: audioContentName
                        }
                    }
                }))
            }
        };

        while (!canFinish) {
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        promptEnd: { promptName }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: { sessionEnd: {} }
                }))
            }
        };
    }

    const command = new InvokeModelWithBidirectionalStreamCommand({
        modelId: 'amazon.nova-2-sonic-v1:0',
        body: inputStream()
    });

    try {
        const response = await client.send(command);
        let firstAudioContentBlock = true;
        let seenAudioOutput = false;

        for await (const event of response.body) {
            if (event.chunk && event.chunk.bytes) {
                const rawEvent = JSON.parse(Buffer.from(event.chunk.bytes).toString());
                const eventData = rawEvent.event || rawEvent;

                if (eventData.audioOutput) {
                    seenAudioOutput = true;
                    // Print prefix so Python subprocess reader can distinguish audio chunks from stdout noise
                    const audioData = eventData.audioOutput.content || eventData.audioOutput;
                    console.log(`AUDIO_CHUNK:${audioData}`);
                }

                if (eventData.completionEnd || (eventData.contentEnd && seenAudioOutput)) {
                    finishSignal();
                }

                if (eventData.internalServerException) {
                    console.error("InternalServerException:", eventData.internalServerException);
                    process.exit(1);
                }
            }
        }
    } catch (e) {
        console.error("Error from Bedrock:", e);
        process.exit(1);
    }
}

main().catch(console.error);

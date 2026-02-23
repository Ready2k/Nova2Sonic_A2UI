// nova_sonic_stt.mjs
//
// Bidirectional Nova 2 Sonic STT (speech-to-text).
// Reads a single base64-encoded PCM 16kHz 16-bit mono audio block from stdin.
// Outputs: TRANSCRIPT:<verbatim transcription>   to stdout
// Debug:   [STT DEBUG] lines                     to stderr
//
// Usage:
//   echo "<base64_audio>" | node nova_sonic_stt.mjs

import {
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamCommand
} from '@aws-sdk/client-bedrock-runtime';
import dotenv from 'dotenv';
import * as path from 'path';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });

const client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1'
});

function nowIso() {
    return new Date().toISOString();
}

async function readStdin() {
    const chunks = [];
    for await (const chunk of process.stdin) {
        chunks.push(chunk);
    }
    return Buffer.concat(chunks).toString('utf8').trim();
}

async function main() {
    const audioB64 = await readStdin();
    if (!audioB64) {
        console.error(`[STT DEBUG] ${nowIso()} No audio data received via stdin`);
        console.log('TRANSCRIPT:');
        process.exit(0);
    }

    console.error(`[STT DEBUG] ${nowIso()} Received audio data length=${audioB64.length}`);

    const promptName = `stt-${Date.now()}`;
    const sysName = `sys-${Date.now()}`;
    const audioName = `audio-${Date.now()}`;

    // Signal from output handler → input generator that we can finish
    let canFinish = false;
    const signalFinish = () => { canFinish = true; };

    async function* inputStream() {
        // Session start
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        sessionStart: {
                            inferenceConfiguration: {
                                maxTokens: 512,
                                topP: 0.9,
                                temperature: 0.1
                            }
                        }
                    }
                }))
            }
        };

        // Prompt start — text output only, but audio output configuration is required by the API
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        promptStart: {
                            promptName,
                            textOutputConfiguration: { mediaType: 'text/plain' },
                            audioOutputConfiguration: {
                                mediaType: 'audio/lpcm',
                                sampleRateHertz: 16000,
                                sampleSizeBits: 16,
                                channelCount: 1,
                                voiceId: 'tiffany'
                            }


                        }
                    }
                }))
            }
        };


        // System content
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: sysName,
                            type: 'TEXT',
                            interactive: false,
                            role: 'SYSTEM',
                            textInputConfiguration: { mediaType: 'text/plain' }
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
                            contentName: sysName,
                            content: 'You are a speech-to-text transcription service. Output ONLY the verbatim words the user spoke — no commentary, no prefix, no explanation.'
                        }
                    }
                }))
            }
        };

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: { contentEnd: { promptName, contentName: sysName } }
                }))
            }
        };

        // Audio content block
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: audioName,
                            type: 'AUDIO',
                            interactive: true,
                            role: 'USER',
                            audioInputConfiguration: {
                                mediaType: 'audio/lpcm',
                                sampleRateHertz: 16000,
                                sampleSizeBits: 16,
                                channelCount: 1,
                                audioType: 'SPEECH',
                                encoding: 'base64'
                            }
                        }
                    }
                }))
            }
        };

        // Send audio in chunks of 32 KB (base64 chars)
        const CHUNK = 32768;
        for (let i = 0; i < audioB64.length; i += CHUNK) {
            yield {
                chunk: {
                    bytes: Buffer.from(JSON.stringify({
                        event: {
                            audioInput: {
                                promptName,
                                contentName: audioName,
                                content: audioB64.slice(i, i + CHUNK)
                            }
                        }
                    }))
                }
            };
        }

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: { contentEnd: { promptName, contentName: audioName } }
                }))
            }
        };

        // Wait for output handler to signal it has the transcript
        while (!canFinish) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: { promptEnd: { promptName } }
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

        let userTranscript = '';
        let inUserTextBlock = false;

        for await (const event of response.body) {
            if (!event.chunk?.bytes) continue;

            const rawEvent = JSON.parse(Buffer.from(event.chunk.bytes).toString());
            const eventData = rawEvent.event || rawEvent;

            // contentStart: track which role/type block we're in
            if (eventData.contentStart) {
                const role = eventData.contentStart.role;
                const type = eventData.contentStart.type;
                console.error(`[STT DEBUG] ${nowIso()} contentStart role=${role} type=${type}`);

                if (role === 'USER' && type === 'TEXT') {
                    inUserTextBlock = true;
                }

                if (role === 'ASSISTANT' && type === 'TEXT') {
                    // ASSISTANT text block starting = transcription is already done;
                    // signal the input generator to close and stop processing.
                    console.error(`[STT DEBUG] ${nowIso()} ASSISTANT text block started — transcription complete`);
                    signalFinish();
                    break;
                }
            }

            // textOutput: accumulate USER-role text (the STT transcript)
            if (eventData.textOutput) {
                const role = eventData.textOutput.role;
                const content = eventData.textOutput.content || '';
                console.error(`[STT DEBUG] ${nowIso()} textOutput role=${role}: "${content}"`);
                if (role === 'USER') {
                    userTranscript += content;
                }
            }

            // contentEnd for USER TEXT block — transcript captured
            if (eventData.contentEnd && inUserTextBlock) {
                inUserTextBlock = false;
                console.error(`[STT DEBUG] ${nowIso()} USER text block ended, transcript="${userTranscript}"`);
                signalFinish();
                // Keep going — wait for promptEnd to close cleanly
            }

            if (eventData.promptEnd) {
                console.error(`[STT DEBUG] ${nowIso()} promptEnd, transcript="${userTranscript}"`);
                signalFinish();
                break;
            }

            if (eventData.internalServerException || eventData.validationException || eventData.throttlingException) {
                console.error(`[STT DEBUG] ${nowIso()} Error event:`, JSON.stringify(eventData));
                signalFinish();
                break;
            }
        }

        if (!canFinish) signalFinish();

        console.log(`TRANSCRIPT:${userTranscript.trim()}`);

    } catch (e) {
        console.error(`[STT DEBUG] ${nowIso()} Error from Bedrock:`, e.message || e);
        console.log('TRANSCRIPT:');
        process.exit(1);
    }
}

main().catch(err => {
    console.error(`[STT DEBUG] Fatal:`, err);
    console.log('TRANSCRIPT:');
    process.exit(1);
});

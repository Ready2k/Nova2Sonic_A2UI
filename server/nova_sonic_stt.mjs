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
import * as readline from 'readline';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });

const client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1'
});

function nowIso() {
    return new Date().toISOString();
}

async function main() {
    const promptName = `stt-${Date.now()}`;
    const sysName = `sys-${Date.now()}`;
    const audioName = `audio-${Date.now()}`;

    // Create a deferred promise for terminal signal from stdin
    let stdinEndedResolver;
    const stdinEndedPromise = new Promise(resolve => { stdinEndedResolver = resolve; });

    // Create a deferred promise for Bedrock completion (optional, for clean promptEnd)
    let bedrockDoneResolver;
    const bedrockDonePromise = new Promise(resolve => { bedrockDoneResolver = resolve; });

    const rl = readline.createInterface({
        input: process.stdin,
        terminal: false
    });

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

        // Prompt start
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

        // Audio content block start
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

        // Use an async iterator to feed stdin lines into the inputStream
        for await (const line of rl) {
            if (!line.trim()) continue;
            yield {
                chunk: {
                    bytes: Buffer.from(JSON.stringify({
                        event: {
                            audioInput: {
                                promptName,
                                contentName: audioName,
                                content: line.trim()
                            }
                        }
                    }))
                }
            };
        }

        console.error(`[STT DEBUG] ${nowIso()} Stdin closed — ending audio content`);
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: { contentEnd: { promptName, contentName: audioName } }
                }))
            }
        };

        // Signal to output loop that we've sent everything
        stdinEndedResolver();

        // Optional: Wait for Bedrock to finish before ending prompt if you want perfectly clean closure
        // await bedrockDonePromise;

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

        let finishedNormally = false;

        let lastEventTime = Date.now();

        for await (const event of response.body) {
            lastEventTime = Date.now();
            if (!event.chunk?.bytes) continue;

            const rawEvent = JSON.parse(Buffer.from(event.chunk.bytes).toString());
            const eventData = rawEvent.event || rawEvent;

            if (eventData.contentStart) {
                const role = eventData.contentStart.role;
                const type = eventData.contentStart.type;
                console.error(`[STT DEBUG] ${nowIso()} contentStart role=${role} type=${type}`);

                if (role === 'USER' && type === 'TEXT') {
                    inUserTextBlock = true;
                }

                if (role === 'ASSISTANT' && type === 'TEXT') {
                    console.error(`[STT DEBUG] ${nowIso()} ASSISTANT text block started — transcription done`);
                    break;
                }
            }

            if (eventData.textOutput) {
                const role = eventData.textOutput.role;
                const content = eventData.textOutput.content || '';
                if (role === 'USER') {
                    userTranscript += content;
                    console.log(`TRANSCRIPT_PARTIAL:${content}`);
                }
            }

            if (eventData.contentEnd && inUserTextBlock) {
                inUserTextBlock = false;
                console.error(`[STT DEBUG] ${nowIso()} USER text block ended`);
            }

            if (eventData.promptEnd) {
                console.error(`[STT DEBUG] ${nowIso()} promptEnd`);
                break;
            }

            if (eventData.internalServerException || eventData.validationException || eventData.throttlingException) {
                console.error(`[STT DEBUG] ${nowIso()} Error event:`, JSON.stringify(eventData));
                break;
            }
        }
        finishedNormally = true;

        bedrockDoneResolver();
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

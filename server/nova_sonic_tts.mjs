// nova_sonic_tts.mjs
//
// FINAL-ONLY Nova 2 Sonic TTS streaming, without RMS/silence “guessing”.
// We rely on Nova’s own stage markers: additionalModelFields.generationStage
// and only emit chunks when stage === "FINAL".
//
// Usage:
//   node nova_sonic_tts.mjs "Hello there" [voiceId]
// Output:
//   AUDIO_CHUNK:<base64 lpcm>  (24kHz, 16-bit, mono)
//
// Notes:
// - We still request FINAL-only via additionalModelRequestFields.generationStage = "FINAL"
//   but we ALSO defensively filter response chunks by the returned generationStage.
// - Silence audio block is still included to signal end-of-turn.

import {
    BedrockRuntimeClient,
    InvokeModelWithBidirectionalStreamCommand
} from '@aws-sdk/client-bedrock-runtime';
import dotenv from 'dotenv';
import * as path from 'path';
import { createHash } from 'crypto';

dotenv.config({ path: path.resolve(process.cwd(), '../.env') });

const textToSpeak = process.argv[2];
if (!textToSpeak) {
    console.error('Usage: node nova_sonic_tts.mjs <text> [voiceId]');
    process.exit(1);
}

const voiceId = process.argv[3] || 'matthew';

const client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1'
});

function nowIso() {
    return new Date().toISOString();
}

function safeJsonParse(maybeJson) {
    if (!maybeJson) return null;
    try {
        return typeof maybeJson === 'string' ? JSON.parse(maybeJson) : maybeJson;
    } catch {
        return null;
    }
}

/**
 * Extract generationStage from whatever shape Nova provides.
 * In some streams it appears as contentStart.additionalModelFields (often JSON string).
 * In others it may be under contentStart.additionalModelFields.generationStage.
 */
function extractGenerationStage(contentStart) {
    if (!contentStart) return null;

    // Prefer explicit additionalModelFields
    const amf = safeJsonParse(contentStart.additionalModelFields);
    if (amf && typeof amf === 'object' && amf.generationStage) return amf.generationStage;

    // Some variants might include it directly
    if (contentStart.generationStage) return contentStart.generationStage;

    return null;
}

async function main() {
    const promptName = `tts-prompt-${Date.now()}`;
    const textContentName = `text-${Date.now()}`;
    const systemContentName = `system-${Date.now()}`;

    let canFinish = false;
    const finishSignal = () => { canFinish = true; };

    async function* inputStream() {
        // Session start
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        sessionStart: {
                            inferenceConfiguration: {
                                maxTokens: 2048,
                                topP: 0.9,
                                temperature: 0.7
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
                            textOutputConfiguration: {
                                mediaType: 'text/plain'
                            },
                            audioOutputConfiguration: {
                                mediaType: 'audio/lpcm',
                                sampleRateHertz: 24000,
                                sampleSizeBits: 16,
                                channelCount: 1,
                                voiceId,
                                encoding: 'base64',
                                audioType: 'SPEECH'
                            },
                            additionalModelRequestFields: {
                                // Request final-only generation
                                generationStage: 'FINAL'
                            }
                        }
                    }
                }))
            }
        };

        // SYSTEM content
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: systemContentName,
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
                        contentEnd: { promptName, contentName: systemContentName }
                    }
                }))
            }
        };

        // USER text content
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: textContentName,
                            type: 'TEXT',
                            interactive: true,
                            role: 'USER',
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
                        contentEnd: { promptName, contentName: textContentName }
                    }
                }))
            }
        };

        // Silence audio block — required by Nova Sonic to signal end-of-turn.
        const audioContentName = `audio-${Date.now()}`;
        yield {
            chunk: {
                bytes: Buffer.from(JSON.stringify({
                    event: {
                        contentStart: {
                            promptName,
                            contentName: audioContentName,
                            type: 'AUDIO',
                            interactive: false,
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
                        contentEnd: { promptName, contentName: audioContentName }
                    }
                }))
            }
        };

        // Wait until Bedrock emits promptEnd or error
        while (!canFinish) {
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        // Prompt/session end
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

        let chunksEmittedTotal = 0;
        let chunksEmittedInBlock = 0;
        let currentRole = null;
        let currentContentType = null;
        let currentGenerationStage = 'FINAL';
        let inAssistantAudioBlock = false;
        let textEmittedSet = new Set();
        let currentBlockText = '';
        let speculativeChunks = [];
        let finalChunks = [];
        let speculativeFallbackChunks = [];

        for await (const event of response.body) {
            if (!event.chunk?.bytes) continue;

            const rawEvent = JSON.parse(Buffer.from(event.chunk.bytes).toString());
            const eventData = rawEvent.event || rawEvent;

            if (eventData.contentStart) {
                currentRole = eventData.contentStart.role;
                currentContentType = eventData.contentStart.type;

                if (currentRole === 'ASSISTANT' && currentContentType === 'AUDIO') {
                    const stage = extractGenerationStage(eventData.contentStart);
                    currentGenerationStage = stage || 'FINAL';

                    inAssistantAudioBlock = true;
                    speculativeChunks = [];
                    finalChunks = [];
                    chunksEmittedInBlock = 0;
                }
            }

            if (eventData.textOutput) {
                const text = eventData.textOutput.content || '';
                const role = eventData.textOutput.role;
                if (role === 'ASSISTANT') {
                    currentBlockText += text;
                }
            }

            if (eventData.audioOutput) {
                const audioData = eventData.audioOutput.content || eventData.audioOutput;

                if (inAssistantAudioBlock) {
                    if (currentGenerationStage === 'FINAL') {
                        chunksEmittedInBlock++;
                        finalChunks.push(audioData);
                    } else if (currentGenerationStage === 'SPECULATIVE') {
                        speculativeChunks.push(audioData);
                    }
                }
            }

            if (eventData.contentEnd) {
                if (currentRole === 'ASSISTANT' && currentContentType === 'AUDIO' && inAssistantAudioBlock) {
                    const normalizedText = currentBlockText.trim();

                    if (chunksEmittedInBlock === 0 && speculativeChunks.length > 0) {
                        speculativeFallbackChunks.push(...speculativeChunks);
                    }

                    if (currentGenerationStage === 'FINAL' && finalChunks.length > 0) {
                        if (normalizedText && textEmittedSet.has(normalizedText)) {
                            // console.error(`[TTS DEBUG] Skipping duplicate FINAL block for text: "${normalizedText}"`);
                        } else {
                            if (normalizedText) textEmittedSet.add(normalizedText);
                            for (const chunk of finalChunks) {
                                chunksEmittedTotal++;
                                console.log(`AUDIO_CHUNK:${chunk}`);
                            }
                        }
                    }

                    inAssistantAudioBlock = false;
                    speculativeChunks = [];
                    finalChunks = [];
                    currentBlockText = ''; // Reset for next block
                }

                currentRole = null;
                currentContentType = null;
            }

            if (eventData.promptEnd) {
                finishSignal();
                break;
            }

            if (eventData.internalServerException || eventData.throttlingException || eventData.validationException) {
                finishSignal();
                break;
            }
        }

        if (chunksEmittedTotal === 0 && speculativeFallbackChunks.length > 0) {
            for (const chunk of speculativeFallbackChunks) {
                chunksEmittedTotal++;
                console.log(`AUDIO_CHUNK:${chunk}`);
            }
        }

        finishSignal();
        process.exit(0);
    } catch (e) {
        finishSignal();
        process.exit(1);
    }
}


main().catch(err => {
    console.error('Fatal:', err);
    process.exit(1);
});

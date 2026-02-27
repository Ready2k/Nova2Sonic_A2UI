// nova_sonic_stt.mjs
//
// Persistent Nova Sonic STT session.
// Stdin command protocol:
//   START_PROMPT            - begin a new user audio prompt
//   END_PROMPT              - end audio for current prompt, trigger transcription
//   INJECT_ASSISTANT:<text> - buffer agent response to include in next prompt as context
//   SESSION_END             - end session and exit
//   <other non-empty line>  - base64 audio chunk
//
// Stdout:
//   TRANSCRIPT_PARTIAL:<text>  - rolling partial transcript
//   TRANSCRIPT:<text>          - final transcript for this turn
//   READY                      - session idle, ready for next START_PROMPT

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

// System prompt reused for every turn
const systemPrompt = process.argv[2] || 'You are a speech-to-text transcription service. Output ONLY the verbatim words the user spoke — no commentary, no prefix, no explanation.';

// AsyncQueue for inter-coroutine communication between stdin pump and inputStream generator
class AsyncQueue {
    constructor() { this._items = []; this._waiters = []; }
    put(item) {
        if (this._waiters.length > 0) this._waiters.shift()(item);
        else this._items.push(item);
    }
    async get() {
        if (this._items.length > 0) return this._items.shift();
        return new Promise(r => this._waiters.push(r));
    }
}

const cmdQueue = new AsyncQueue();
let promptIndex = 0;

// Buffered assistant text to inject into the NEXT prompt (before audio)
// This avoids trying to add content after promptEnd has been sent.
let pendingAssistantInject = null;

const rl = readline.createInterface({ input: process.stdin, terminal: false });

// Stdin pump — routes lines to cmdQueue
rl.on('line', (line) => {
    const t = line.trim();
    if (!t) return;
    if (t === 'START_PROMPT')
        cmdQueue.put({ type: 'start' });
    else if (t === 'END_PROMPT')
        cmdQueue.put({ type: 'end' });
    else if (t.startsWith('INJECT_ASSISTANT:'))
        cmdQueue.put({ type: 'inject_assistant', text: t.slice('INJECT_ASSISTANT:'.length) });
    else if (t === 'SESSION_END')
        cmdQueue.put({ type: 'session_end' });
    else
        cmdQueue.put({ type: 'audio', data: t });
});
rl.on('close', () => cmdQueue.put({ type: 'session_end' }));

function makeEvent(obj) {
    return { chunk: { bytes: Buffer.from(JSON.stringify({ event: obj })) } };
}

async function* inputStream() {
    // Session start — sent exactly once
    yield makeEvent({
        sessionStart: {
            inferenceConfiguration: {
                maxTokens: 512,
                topP: 0.9,
                temperature: 0.1
            }
        }
    });

    while (true) {
        const cmd = await cmdQueue.get();

        if (cmd.type === 'inject_assistant') {
            // Buffer for inclusion in the NEXT prompt (before user audio)
            pendingAssistantInject = cmd.text;
            console.error(`[STT DEBUG] ${nowIso()} Buffered assistant inject (${cmd.text.length} chars) for next prompt`);
            continue;
        }

        if (cmd.type === 'session_end') {
            yield makeEvent({ sessionEnd: {} });
            return;
        }

        if (cmd.type !== 'start') continue;

        // Begin a new prompt turn
        const promptName = `stt-${Date.now()}-${++promptIndex}`;
        const sysName = `sys-${promptName}`;
        const audioName = `audio-${promptName}`;

        console.error(`[STT DEBUG] ${nowIso()} Starting prompt ${promptName}`);

        yield makeEvent({
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
        });

        // System content block
        yield makeEvent({
            contentStart: {
                promptName,
                contentName: sysName,
                type: 'TEXT',
                interactive: false,
                role: 'SYSTEM',
                textInputConfiguration: { mediaType: 'text/plain' }
            }
        });
        yield makeEvent({ textInput: { promptName, contentName: sysName, content: systemPrompt } });
        yield makeEvent({ contentEnd: { promptName, contentName: sysName } });

        // If a previous assistant response was buffered, inject it before user audio
        // so Nova Sonic has bilateral conversation context for better transcription
        if (pendingAssistantInject) {
            const assistantContentName = `assistant-${promptName}`;
            console.error(`[STT DEBUG] ${nowIso()} Injecting buffered assistant context into ${promptName}`);
            yield makeEvent({
                contentStart: {
                    promptName,
                    contentName: assistantContentName,
                    type: 'TEXT',
                    interactive: false,
                    role: 'ASSISTANT',
                    textInputConfiguration: { mediaType: 'text/plain' }
                }
            });
            yield makeEvent({ textInput: { promptName, contentName: assistantContentName, content: pendingAssistantInject } });
            yield makeEvent({ contentEnd: { promptName, contentName: assistantContentName } });
            pendingAssistantInject = null;
        }

        // Audio content block
        yield makeEvent({
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
        });

        // Stream audio chunks until END_PROMPT (or clean shutdown)
        let chunkCount = 0;
        audioLoop: while (true) {
            const ac = await cmdQueue.get();
            if (ac.type === 'audio') {
                chunkCount++;
                if (chunkCount % 10 === 0) {
                    console.error(`[STT DEBUG] ${nowIso()} ${chunkCount} audio chunks for ${promptName}`);
                }
                yield makeEvent({ audioInput: { promptName, contentName: audioName, content: ac.data } });
            } else if (ac.type === 'end') {
                break audioLoop;
            } else if (ac.type === 'session_end') {
                // Clean shutdown mid-stream
                yield makeEvent({ contentEnd: { promptName, contentName: audioName } });
                yield makeEvent({ promptEnd: { promptName } });
                yield makeEvent({ sessionEnd: {} });
                return;
            } else if (ac.type === 'inject_assistant') {
                // Buffer for the next prompt
                pendingAssistantInject = ac.text;
            }
            // ignore stray 'start' while audio is in flight
        }

        console.error(`[STT DEBUG] ${nowIso()} END_PROMPT received for ${promptName} (${chunkCount} chunks)`);
        yield makeEvent({ contentEnd: { promptName, contentName: audioName } });

        // Send promptEnd IMMEDIATELY — do NOT wait for Bedrock's promptEnd first.
        // Bedrock needs to receive our promptEnd before it will generate a response
        // and send back its own promptEnd. Waiting here would cause a deadlock.
        yield makeEvent({ promptEnd: { promptName } });

        // Loop back to wait for next START_PROMPT or INJECT_ASSISTANT
    }
}

async function main() {
    const command = new InvokeModelWithBidirectionalStreamCommand({
        modelId: 'amazon.nova-2-sonic-v1:0',
        body: inputStream()
    });

    try {
        const response = await client.send(command);

        let userTranscript = '';
        let inUserTextBlock = false;
        let userTextContentName = null;
        let transcriptEmittedForCurrentPrompt = false;

        for await (const event of response.body) {
            if (!event.chunk?.bytes) continue;

            const rawEvent = JSON.parse(Buffer.from(event.chunk.bytes).toString());
            const eventData = rawEvent.event || rawEvent;

            if (eventData.contentStart) {
                const role = eventData.contentStart.role;
                const type = eventData.contentStart.type;
                const name = eventData.contentStart.contentName;
                console.error(`[STT DEBUG] ${nowIso()} contentStart role=${role} type=${type}`);
                if (role === 'USER' && type === 'TEXT') {
                    // New USER text block = new prompt turn. Reset all per-prompt state so that
                    // a stale transcriptEmittedForCurrentPrompt (from a previous prompt whose
                    // Bedrock promptEnd hasn't arrived yet) does not suppress this transcript.
                    inUserTextBlock = true;
                    userTextContentName = name;
                    userTranscript = '';
                    transcriptEmittedForCurrentPrompt = false;
                }
            }

            if (eventData.textOutput) {
                const role = eventData.textOutput.role;
                const content = eventData.textOutput.content || '';
                if (role === 'USER' && content) {
                    userTranscript += content;
                    console.log(`TRANSCRIPT_PARTIAL:${userTranscript.trim()}`);
                }
            }

            // Emit TRANSCRIPT as soon as the USER TEXT block closes.
            // Do NOT wait for promptEnd — Nova Sonic generates ASSISTANT audio after
            // transcription, and promptEnd only arrives after that audio is done (~20s).
            if (eventData.contentEnd) {
                const name = eventData.contentEnd.contentName;
                if (inUserTextBlock && name === userTextContentName) {
                    inUserTextBlock = false;
                    userTextContentName = null;
                    if (!transcriptEmittedForCurrentPrompt) {
                        transcriptEmittedForCurrentPrompt = true;
                        console.error(`[STT DEBUG] ${nowIso()} USER text block closed — emitting TRANSCRIPT`);
                        console.log(`TRANSCRIPT:${userTranscript.trim()}`);
                        userTranscript = '';
                        console.log('READY');
                    }
                }
            }

            if (eventData.promptEnd) {
                console.error(`[STT DEBUG] ${nowIso()} promptEnd received from Bedrock`);
                // If for some reason the USER text block never closed (edge case), emit now
                if (!transcriptEmittedForCurrentPrompt) {
                    transcriptEmittedForCurrentPrompt = true;
                    console.log(`TRANSCRIPT:${userTranscript.trim()}`);
                    userTranscript = '';
                    console.log('READY');
                }
                transcriptEmittedForCurrentPrompt = false; // reset for next prompt
                // Signal Python that Bedrock has fully finished this prompt —
                // safe to send START_PROMPT for the next turn now.
                console.log('BEDROCK_DONE');
                continue; // DO NOT break — session persists for next prompt
            }

            if (eventData.sessionEnd) {
                console.error(`[STT DEBUG] ${nowIso()} sessionEnd`);
                break;
            }

            if (eventData.internalServerException || eventData.validationException || eventData.throttlingException) {
                console.error(`[STT DEBUG] ${nowIso()} Error event:`, JSON.stringify(eventData));
                // Unblock Python side to prevent hanging
                if (!transcriptEmittedForCurrentPrompt) {
                    console.log('TRANSCRIPT:');
                    console.log('READY');
                }
                console.log('BEDROCK_DONE');
                break;
            }
        }
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

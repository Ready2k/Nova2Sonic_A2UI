import { useState, useEffect, useRef, useCallback, useId } from 'react';
import { A2UIPayload } from '../components/A2Renderer';

export interface ActionPayload {
    actionId: string;
    data?: Record<string, unknown>;
}

class AudioStreamer {
    private audioContext: AudioContext;
    private nextStartTime: number | null = null;
    private chunkQueue: string[] = [];
    private isProcessing: boolean = false;
    private isAcceptingChunks: boolean = true;
    private lastSource: AudioBufferSourceNode | null = null;

    constructor() {
        this.audioContext = new (window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)({ sampleRate: 24000 });
        console.log('[AudioStreamer] Created with state:', this.audioContext.state);
    }

    private async ensureResumed() {
        if (this.audioContext.state === 'suspended') {
            try {
                await this.audioContext.resume();
                console.log('[AudioStreamer] Resumed audio context');
            } catch (err) {
                console.error('[AudioStreamer] Failed to resume:', err);
                throw err;
            }
        }
    }

    private async processQueue() {
        if (this.isProcessing) return;
        this.isProcessing = true;
        try {
            while (this.chunkQueue.length > 0) {
                const chunk = this.chunkQueue.shift();
                if (chunk) {
                    await this._playChunk(chunk);
                }
            }
        } finally {
            this.isProcessing = false;
        }
    }

    /** Resolves once the queue is empty and no processing is in flight. */
    public waitForQueueDrained(): Promise<void> {
        return new Promise((resolve) => {
            const check = () => {
                if (!this.isProcessing && this.chunkQueue.length === 0) {
                    resolve();
                } else {
                    setTimeout(check, 10);
                }
            };
            check();
        });
    }

    private async _playChunk(base64: string) {
        try {
            await this.ensureResumed();

            const binaryString = window.atob(base64);
            const len = binaryString.length;
            const bytes = new Uint8Array(len);
            for (let i = 0; i < len; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }

            const int16Data = new Int16Array(bytes.buffer);
            const float32Data = new Float32Array(int16Data.length);
            for (let i = 0; i < int16Data.length; i++) {
                float32Data[i] = int16Data[i] / 32768.0;
            }

            const audioBuffer = this.audioContext.createBuffer(1, float32Data.length, 24000);
            audioBuffer.getChannelData(0).set(float32Data);

            const source = this.audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this.audioContext.destination);

            if (this.nextStartTime === null) {
                this.nextStartTime = this.audioContext.currentTime;
                console.log('[AudioStreamer] First chunk - scheduling from', this.nextStartTime);
            }

            const currentTime = this.audioContext.currentTime;
            if (this.nextStartTime < currentTime) {
                console.warn('[AudioStreamer] Scheduling time is in past, resetting. current:', currentTime, 'next:', this.nextStartTime);
                this.nextStartTime = currentTime + 0.01;
            }

            source.start(this.nextStartTime);
            this.nextStartTime += audioBuffer.duration;
            this.lastSource = source; // Always track the last scheduled source
        } catch (err) {
            console.error('[AudioStreamer] Error playing chunk:', err);
            throw err;
        }
    }

    public async playChunk(base64: string) {
        if (!this.isAcceptingChunks) {
            console.warn('[AudioStreamer] Not accepting new chunks');
            return;
        }
        this.chunkQueue.push(base64);
        await this.processQueue();
    }

    /**
     * Graceful finish: stop accepting chunks, let the queue fully drain,
     * then wait for the last AudioBufferSourceNode to fire `onended` before
     * closing the context and calling onDone. A generous timer fallback is
     * included in case `onended` doesn't fire (e.g. context closed externally).
     */
    public finishPlayback(onDone: () => void) {
        this.isAcceptingChunks = false;
        let settled = false;

        const cleanup = () => {
            if (settled) return;
            settled = true;
            console.log('[AudioStreamer] Playback complete — closing context');
            if (this.audioContext.state !== 'closed') {
                this.audioContext.close().catch(() => { });
            }
            this.nextStartTime = null;
            this.isProcessing = false;
            onDone();
        };

        this.waitForQueueDrained().then(() => {
            const remainingMs = this.getScheduledDurationMs();
            console.log(`[AudioStreamer] Queue drained; ${remainingMs.toFixed(0)} ms of audio remaining`);

            if (remainingMs <= 50 || !this.lastSource) {
                // Nothing left scheduled (or never started)
                cleanup();
                return;
            }

            // Primary signal: fires precisely when the last buffer finishes
            this.lastSource.onended = cleanup;
            // Safety net: close no earlier than remainingMs + 3 s buffer
            setTimeout(cleanup, remainingMs + 3000);
        });
    }

    /** Immediate stop — use for interrupts or disconnect. */
    public stop() {
        console.log('[AudioStreamer] Immediate stop, context state:', this.audioContext.state);
        this.isAcceptingChunks = false;
        this.chunkQueue = [];
        if (this.audioContext.state !== 'closed') {
            try {
                this.audioContext.close();
            } catch (err) {
                console.error('[AudioStreamer] Error closing context:', err);
            }
        }
        this.nextStartTime = null;
        this.isProcessing = false;
    }

    /** Milliseconds until the last scheduled buffer finishes. */
    public getScheduledDurationMs(): number {
        if (this.nextStartTime === null) return 0;
        const remaining = (this.nextStartTime - this.audioContext.currentTime) * 1000;
        return Math.max(0, remaining);
    }
}

export function useMortgageSocket(url: string) {
    const [socket, setSocket] = useState<WebSocket | null>(null);
    const [connected, setConnected] = useState(false);
    // Do not auto-connect on mount; require user to call `connect()` manually
    const [shouldConnect, setShouldConnect] = useState(false);
    const [messages, setMessages] = useState<{ role: 'user' | 'assistant', text: string, image?: string }[]>([]);
    const [voicePlaying, setVoicePlaying] = useState(false);
    const [a2uiState, setA2uiState] = useState<A2UIPayload | null>(null);
    const [thinkingState, setThinkingState] = useState<string | null>(null);
    const [volume, setVolume] = useState(0);
    const [partialTranscript, setPartialTranscript] = useState('');
    const [mode, setModeState] = useState<'text' | 'voice'>('text');
    const modeRef = useRef<'text' | 'voice'>('text');


    const [ttfb, setTtfb] = useState<number | null>(null);
    const [uiPatchLatency, setUiPatchLatency] = useState<number | null>(null);
    const [voiceLatency, setVoiceLatency] = useState<number | null>(null);
    const [isRecording, setIsRecording] = useState(false);

    const VAD_THRESHOLD = 0.015;
    const VAD_SILENCE_TIMEOUT = 1500;

    const hookId = useId();

    const requestStartRef = useRef<number>(0);
    const ttfbRef = useRef<number | null>(null);
    const uiPatchLatencyRef = useRef<number | null>(null);
    const clientSessionIdRef = useRef<string>('');
    const streamerRef = useRef<AudioStreamer | null>(null);
    const recordingContextRef = useRef<AudioContext | null>(null);
    const recordingStreamRef = useRef<MediaStream | null>(null);
    const recordingProcessorRef = useRef<ScriptProcessorNode | null>(null);
    const lastSpeechTimeRef = useRef<number>(0);
    const hasSpokenRef = useRef<boolean>(false);

    useEffect(() => { ttfbRef.current = ttfb; }, [ttfb]);
    useEffect(() => { uiPatchLatencyRef.current = uiPatchLatency; }, [uiPatchLatency]);
    useEffect(() => {
        if (!clientSessionIdRef.current) {
            clientSessionIdRef.current = `client-${hookId}`;
        }
    }, [hookId]);

    const stopAudioBuffer = useCallback(() => {
        // Immediate stop — used on disconnect or component unmount.
        if (streamerRef.current) {
            console.log('[Hook] Stopping audio immediately (disconnect/unmount)');
            streamerRef.current.stop();
            streamerRef.current = null;
        }
        setVoicePlaying(false);
    }, []);

    const stopRecording = useCallback(() => {
        if (recordingProcessorRef.current) {
            recordingProcessorRef.current.disconnect();
            recordingProcessorRef.current = null;
        }
        if (recordingStreamRef.current) {
            recordingStreamRef.current.getTracks().forEach(track => track.stop());
            recordingStreamRef.current = null;
        }
        if (recordingContextRef.current && recordingContextRef.current.state !== 'closed') {
            recordingContextRef.current.close();
            recordingContextRef.current = null;
        }
    }, []);

    useEffect(() => {
        if (!shouldConnect) return;

        const ws = new WebSocket(url);

        ws.onopen = () => {
            setConnected(true);
            ws.send(JSON.stringify({ type: 'client.hello', sessionId: clientSessionIdRef.current }));
            // Also inform the server of the current UI mode (voice/text)
            ws.send(JSON.stringify({
                type: 'client.mode.update',
                sessionId: clientSessionIdRef.current,
                payload: { mode: modeRef.current }
            }));
            setSocket(ws);
        };


        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const { type, payload } = data;

            if (!ttfbRef.current && requestStartRef.current) {
                setTtfb(Date.now() - requestStartRef.current);
            }

            if (type === 'server.ready') {
                console.log('Server is ready');
            } else if (type === 'server.transcript.partial') {
                setPartialTranscript(prev => prev + (prev ? ' ' : '') + payload.text);
            } else if (type === 'server.transcript.final') {
                const role = (payload.role || 'user') as 'user' | 'assistant';
                if (role === 'user') {
                    setPartialTranscript('');
                }
                setMessages(prev => {
                    const last = prev[prev.length - 1];
                    if (last && last.text === payload.text && last.role === role) return prev;
                    return [...prev, { text: payload.text, role, image: payload.image }];
                });
            } else if (type === 'server.agent.thinking') {
                setThinkingState(payload.state);
            } else if (type === 'server.voice.start') {
                // Server signals TTS is about to begin — show Speaking indicator immediately
                // before the first audio chunk arrives (subprocess takes a few seconds to start)
                if (!streamerRef.current) {
                    streamerRef.current = new AudioStreamer();
                }
                setVoicePlaying(true);
                if (requestStartRef.current) {
                    setVoiceLatency(Date.now() - requestStartRef.current);
                }
            } else if (type === 'server.voice.audio') {
                console.log('[WebSocket] Received server.voice.audio, streamer exists:', !!streamerRef.current);
                if (!streamerRef.current) {
                    console.log('[WebSocket] Creating new AudioStreamer');
                    streamerRef.current = new AudioStreamer();
                    setVoicePlaying(true);
                    if (requestStartRef.current) {
                        setVoiceLatency(Date.now() - requestStartRef.current);
                    }
                }
                if (payload.data) {
                    console.log('[WebSocket] Queuing audio chunk, size:', payload.data.length);
                    streamerRef.current.playChunk(payload.data).catch(err => {
                        console.error('[WebSocket] Error playing audio chunk:', err);
                    });
                }
            } else if (type === 'server.voice.stop') {
                console.log('[WebSocket] Received server.voice.stop, streamer exists:', !!streamerRef.current);
                if (streamerRef.current) {
                    const oldStreamer = streamerRef.current;
                    // Null out immediately so the next server.voice.audio creates a fresh streamer.
                    streamerRef.current = null;
                    // Let all queued chunks finish scheduling, then wait for the last
                    // AudioBufferSourceNode.onended before closing the context.
                    oldStreamer.finishPlayback(() => {
                        setVoicePlaying(false);
                        // Auto-restart listening if in voice mode and the server isn't thinking
                        if (modeRef.current === 'voice' && connected) {
                            console.log('[Auto-Restart] Voice playback finished, re-enabling mic');
                            sendAudioStart().catch(err => console.error('[Auto-Restart] Failed:', err));
                        }
                    });
                }
            } else if (type === 'server.a2ui.patch') {
                if (!uiPatchLatencyRef.current && requestStartRef.current) {
                    setUiPatchLatency(Date.now() - requestStartRef.current);
                }
                setA2uiState(payload);
                setThinkingState(null);
            } else if (type === 'server.error') {
                console.error("Server error:", payload.detail);
            }
        };

        ws.onclose = () => {
            setConnected(false);
            stopAudioBuffer();
            stopRecording();
        };

        return () => {
            ws.close();
            stopAudioBuffer();
            stopRecording();
        };
    }, [url, shouldConnect, stopAudioBuffer, stopRecording]);

    const connect = useCallback(() => setShouldConnect(true), []);
    const disconnect = useCallback(() => setShouldConnect(false), []);

    const sendAction = (actionId: string, data?: Record<string, unknown>) => {
        if (!socket) return;
        requestStartRef.current = Date.now();
        setTtfb(null);
        setUiPatchLatency(null);
        socket.send(JSON.stringify({
            type: 'client.ui.action',
            sessionId: clientSessionIdRef.current,
            payload: { id: actionId, data }
        }));
    };

    const sendText = (text: string, image?: string) => {
        if (!socket) return;
        requestStartRef.current = Date.now();
        setTtfb(null);
        setUiPatchLatency(null);
        setVoiceLatency(null);
        // Don't stop audio buffer - let the playback finish naturally
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                type: 'client.text',
                sessionId: clientSessionIdRef.current,
                payload: { text, image }
            }));
        }
    };

    const sendAudioStart = async () => {
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            throw new Error("WebSocket is not connected");
        }

        requestStartRef.current = Date.now();
        setTtfb(null);
        setUiPatchLatency(null);
        setVoiceLatency(null);
        setPartialTranscript('');
        setIsRecording(true);
        lastSpeechTimeRef.current = Date.now();
        hasSpokenRef.current = false;

        // If audio is playing, stop it immediately and tell the server
        if (streamerRef.current) {
            socket.send(JSON.stringify({
                type: 'client.audio.interrupt',
                sessionId: clientSessionIdRef.current
            }));
            streamerRef.current.stop();
            streamerRef.current = null;
            setVoicePlaying(false);
        }

        socket.send(JSON.stringify({
            type: 'client.audio.start',
            sessionId: clientSessionIdRef.current
        }));

        try {
            // Create AudioContext BEFORE getUserMedia to avoid suspension rules on some browsers
            const AudioContextClass = window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
            const audioCtx = new AudioContextClass({ sampleRate: 16000 });
            recordingContextRef.current = audioCtx;

            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            recordingStreamRef.current = stream;

            // Resume audioCtx if it was created in a suspended state
            if (audioCtx.state === 'suspended') {
                await audioCtx.resume();
            }

            const source = audioCtx.createMediaStreamSource(stream);

            const workletCode = `
class PCM16Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.bufferSize = 4096;
    this.buffer = new Int16Array(this.bufferSize);
    this.bufferIndex = 0;
    this.lastVolumeSampleTime = 0;
  }
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input && input.length > 0) {
      const float32Data = input[0];
      let sumSq = 0;
      for (let i = 0; i < float32Data.length; i++) {
        const sample = float32Data[i];
        sumSq += sample * sample;
        this.buffer[this.bufferIndex++] = Math.max(-32768, Math.min(32767, Math.floor(sample * 32768)));
        if (this.bufferIndex >= this.bufferSize) {
          const outBuffer = new Int16Array(this.buffer);
          this.port.postMessage({ type: 'audio', buffer: outBuffer.buffer }, [outBuffer.buffer]);
          this.bufferIndex = 0;
        }
      }
      
      const currentTime = Date.now();
      if (currentTime - this.lastVolumeSampleTime > 100) {
        const rms = Math.sqrt(sumSq / float32Data.length);
        this.port.postMessage({ type: 'volume', volume: rms });
        this.lastVolumeSampleTime = currentTime;
      }
    }
    return true;
  }
}
registerProcessor('pcm16-processor', PCM16Processor);

`;
            const blob = new Blob([workletCode], { type: 'application/javascript' });
            const workletUrl = URL.createObjectURL(blob);
            await audioCtx.audioWorklet.addModule(workletUrl);

            const workletNode = new AudioWorkletNode(audioCtx, 'pcm16-processor');
            recordingProcessorRef.current = workletNode as unknown as ScriptProcessorNode;

            workletNode.port.onmessage = (e) => {
                if (e.data.type === 'volume') {
                    const vol = e.data.volume;
                    setVolume(vol);

                    // --- VAD Logic ---
                    if (vol > VAD_THRESHOLD) {
                        lastSpeechTimeRef.current = Date.now();
                        hasSpokenRef.current = true;
                    } else if (hasSpokenRef.current) {
                        const silenceMs = Date.now() - lastSpeechTimeRef.current;
                        if (silenceMs > VAD_SILENCE_TIMEOUT) {
                            console.log(`[VAD] Silence of ${silenceMs}ms detected (threshold ${VAD_THRESHOLD}). Auto-stopping.`);
                            sendAudioStop();
                        }
                    }
                    return;
                }

                const buffer = e.data.buffer;
                const int16Array = new Int16Array(buffer);
                const uint8Array = new Uint8Array(int16Array.buffer);
                let binary = '';
                for (let i = 0; i < uint8Array.length; i++) {
                    binary += String.fromCharCode(uint8Array[i]);
                }
                const base64 = btoa(binary);


                if (socket && socket.readyState === WebSocket.OPEN) {
                    console.log("[Audio] Sending chunk of size", base64.length);
                    socket.send(JSON.stringify({
                        type: 'client.audio.chunk',
                        sessionId: clientSessionIdRef.current,
                        payload: { data: base64 }
                    }));
                }
            };

            source.connect(workletNode);
            // Connect to destination so the browser actually processes the audio graph
            workletNode.connect(audioCtx.destination);
        } catch (err) {
            setIsRecording(false);
            console.error("Microphone access denied or error", err);
            throw err;
        }
    };

    const sendAudioStop = () => {
        if (!socket) return;
        requestStartRef.current = Date.now();

        stopRecording();
        setVolume(0);


        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                type: 'client.audio.stop',
                sessionId: clientSessionIdRef.current
            }));
        }
        setIsRecording(false);
    };

    const sendModeUpdate = useCallback((newMode: 'text' | 'voice') => {
        setModeState(newMode);
        modeRef.current = newMode;
        if (!socket || socket.readyState !== WebSocket.OPEN) return;
        console.log('[Hook] Sending mode update:', newMode);
        socket.send(JSON.stringify({
            type: 'client.mode.update',
            sessionId: clientSessionIdRef.current,
            payload: { mode: newMode }
        }));
    }, [socket]);

    return {
        connected,
        messages,
        voicePlaying,
        a2uiState,
        thinkingState,
        partialTranscript,
        sendAction,
        sendText,
        sendAudioStart,
        sendAudioStop,
        sendModeUpdate,
        connect,
        disconnect,
        volume,
        isRecording,
        mode,
        latency: { ttfb, uiPatchLatency, voiceLatency }
    };

}

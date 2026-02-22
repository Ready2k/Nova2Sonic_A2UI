import { useState, useEffect, useRef, useCallback, useId } from 'react';

export interface ActionPayload {
    actionId: string;
    data?: Record<string, unknown>;
}

class AudioStreamer {
    private audioContext: AudioContext;
    private nextStartTime: number = 0;

    constructor() {
        this.audioContext = new (window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)({ sampleRate: 24000 });
    }

    public playChunk(base64: string) {
        if (this.audioContext.state === 'suspended') {
            this.audioContext.resume();
        }

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

        const currentTime = this.audioContext.currentTime;
        if (this.nextStartTime < currentTime) {
            this.nextStartTime = currentTime;
        }

        source.start(this.nextStartTime);
        this.nextStartTime += audioBuffer.duration;
    }

    public stop() {
        if (this.audioContext.state !== 'closed') {
            this.audioContext.close();
        }
    }
}

export function useMortgageSocket(url: string) {
    const [socket, setSocket] = useState<WebSocket | null>(null);
    const [connected, setConnected] = useState(false);
    const [shouldConnect, setShouldConnect] = useState(true);
    const [transcript, setTranscript] = useState<{ text: string, role: string } | null>(null);
    const [voicePlaying, setVoicePlaying] = useState(false);
    const [a2uiState, setA2uiState] = useState<Record<string, unknown> | null>(null);
    const [thinkingState, setThinkingState] = useState<string | null>(null);

    const [ttfb, setTtfb] = useState<number | null>(null);
    const [uiPatchLatency, setUiPatchLatency] = useState<number | null>(null);
    const [voiceLatency, setVoiceLatency] = useState<number | null>(null);

    const hookId = useId();

    const requestStartRef = useRef<number>(0);
    const ttfbRef = useRef<number | null>(null);
    const uiPatchLatencyRef = useRef<number | null>(null);
    const clientSessionIdRef = useRef<string>('');
    const streamerRef = useRef<AudioStreamer | null>(null);
    const recordingContextRef = useRef<AudioContext | null>(null);
    const recordingStreamRef = useRef<MediaStream | null>(null);
    const recordingProcessorRef = useRef<ScriptProcessorNode | null>(null);

    useEffect(() => { ttfbRef.current = ttfb; }, [ttfb]);
    useEffect(() => { uiPatchLatencyRef.current = uiPatchLatency; }, [uiPatchLatency]);
    useEffect(() => {
        if (!clientSessionIdRef.current) {
            clientSessionIdRef.current = `client-${hookId}`;
        }
    }, [hookId]);

    const stopAudioBuffer = useCallback(() => {
        if (streamerRef.current) {
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
            } else if (type === 'server.transcript.final') {
                setTranscript({ text: payload.text, role: payload.role || 'user' });
            } else if (type === 'server.agent.thinking') {
                setThinkingState(payload.state);
            } else if (type === 'server.voice.audio') {
                if (!streamerRef.current) {
                    streamerRef.current = new AudioStreamer();
                    setVoicePlaying(true);
                    if (requestStartRef.current) {
                        setVoiceLatency(Date.now() - requestStartRef.current);
                    }
                }
                if (payload.data) {
                    streamerRef.current.playChunk(payload.data);
                }
            } else if (type === 'server.voice.stop') {
                setTimeout(() => setVoicePlaying(false), 2000);
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
        stopAudioBuffer();
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

        if (voicePlaying) {
            stopAudioBuffer();
            socket.send(JSON.stringify({
                type: 'client.audio.interrupt',
                sessionId: clientSessionIdRef.current
            }));
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
  }
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input && input.length > 0) {
      const float32Data = input[0];
      for (let i = 0; i < float32Data.length; i++) {
        this.buffer[this.bufferIndex++] = Math.max(-32768, Math.min(32767, Math.floor(float32Data[i] * 32768)));
        if (this.bufferIndex >= this.bufferSize) {
          const outBuffer = new Int16Array(this.buffer);
          this.port.postMessage(outBuffer.buffer, [outBuffer.buffer]);
          this.bufferIndex = 0;
        }
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
                const buffer = e.data;
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
            console.error("Microphone access denied or error", err);
            throw err;
        }
    };

    const sendAudioStop = () => {
        if (!socket) return;
        requestStartRef.current = Date.now();

        stopRecording();

        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                type: 'client.audio.stop',
                sessionId: clientSessionIdRef.current
            }));
        }
    };

    return {
        connected,
        transcript,
        voicePlaying,
        a2uiState,
        thinkingState,
        sendAction,
        sendText,
        sendAudioStart,
        sendAudioStop,
        connect,
        disconnect,
        latency: { ttfb, uiPatchLatency, voiceLatency }
    };
}

"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import A2Renderer, { A2UIPayload } from "../../components/A2Renderer";

// ─── Types ────────────────────────────────────────────────────────────────────
interface ChatMessage {
    role: "user" | "assistant";
    text: string;
    ts: number;
}

// ─── WebSocket hook (chat-only, no voice/Nova Sonic) ─────────────────────────
function useChatSocket(url: string) {
    const [connected, setConnected] = useState(false);
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [a2uiState, setA2uiState] = useState<A2UIPayload | null>(null);
    const [thinking, setThinking] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const addMessage = useCallback((role: "user" | "assistant", text: string) => {
        setMessages(prev => {
            const last = prev[prev.length - 1];
            if (last && last.role === role && last.text === text) return prev; // dedup
            return [...prev, { role, text, ts: Date.now() }];
        });
    }, []);

    const connect = useCallback(function connectSocket() {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
            setConnected(true);
            ws.send(JSON.stringify({ type: "client.hello", payload: {} }));
        };

        ws.onclose = () => {
            setConnected(false);
            // Reconnect after 2 s
            reconnectRef.current = setTimeout(connectSocket, 2000);
        };

        ws.onerror = () => ws.close();

        ws.onmessage = (ev) => {
            try {
                const msg = JSON.parse(ev.data);
                const { type, payload } = msg;

                switch (type) {
                    case "server.a2ui.patch": {
                        const patch = payload as A2UIPayload;
                        if (patch?.updateComponents) setA2uiState(patch);
                        break;
                    }
                    case "server.transcript.final": {
                        const text = payload?.text;
                        const role = payload?.role ?? "assistant";
                        if (text) addMessage(role as "user" | "assistant", text);
                        break;
                    }
                    case "server.agent.thinking":
                        setThinking(payload?.state !== "idle");
                        break;
                    case "server.voice.stop":
                        setThinking(false);
                        break;
                    default:
                        break;
                }
            } catch {
                // ignore malformed frames
            }
        };
    }, [url, addMessage]);

    useEffect(() => {
        connect();
        return () => {
            if (reconnectRef.current) clearTimeout(reconnectRef.current);
            wsRef.current?.close();
        };
    }, [connect]);

    const sendText = useCallback((text: string) => {
        if (wsRef.current?.readyState !== WebSocket.OPEN) return;
        addMessage("user", text);
        setThinking(true);
        wsRef.current.send(JSON.stringify({
            type: "client.text",
            payload: { text },
        }));
    }, [addMessage]);

    const sendAction = useCallback((id: string, data: Record<string, unknown>) => {
        if (wsRef.current?.readyState !== WebSocket.OPEN) return;
        setThinking(true);
        wsRef.current.send(JSON.stringify({
            type: "client.ui.action",
            payload: { id, data },
        }));
    }, []);

    return { connected, messages, a2uiState, thinking, sendText, sendAction };
}

// ─── Chat Page ────────────────────────────────────────────────────────────────
export default function ChatPage() {
    const wsUrl =
        (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws")
            .replace("/ws", "/ws/chat");

    const { connected, messages, a2uiState, thinking, sendText, sendAction } =
        useChatSocket(wsUrl);

    const [input, setInput] = useState("");
    const bottomRef = useRef<HTMLDivElement>(null);

    useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

    const handleSend = () => {
        const text = input.trim();
        if (!text || !connected) return;
        sendText(text);
        setInput("");
    };

    const handleKey = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
    };


    return (
        <div className="chat-root">
            {/* ── Header ─────────────────────────────────────────────────────── */}
            <header className="chat-header">
                <div className="chat-header-inner">
                    <div className="chat-logo">
                        <span className="chat-logo-icon">🏠</span>
                        <span className="chat-logo-text">Barclays Mortgage Assistant</span>
                        <span className="chat-badge">Chat Mode</span>
                    </div>
                    <div className={`chat-status ${connected ? "online" : "offline"}`}>
                        <span className="chat-status-dot" />
                        {connected ? "Connected" : "Reconnecting…"}
                    </div>
                </div>
            </header>

            <main className="chat-main">
                {/* ── A2UI Panel ──────────────────────────────────────────────── */}
                <aside className="chat-sidebar">
                    <div className="chat-sidebar-label">Live Summary</div>
                    <div className="chat-a2ui-panel">
                        {a2uiState ? (
                            <A2Renderer
                                a2uiState={a2uiState}
                                onAction={(id, data) => sendAction(id, data as Record<string, unknown>)}
                            />
                        ) : (
                            <div className="chat-a2ui-empty">
                                <div className="chat-a2ui-icon">📋</div>
                                <p>Your mortgage details will appear here as the conversation progresses.</p>
                            </div>
                        )}
                    </div>
                </aside>

                {/* ── Chat Panel ──────────────────────────────────────────────── */}
                <section className="chat-panel">
                    <div className="chat-messages">
                        {messages.length === 0 && (
                            <div className="chat-empty-state">
                                <div className="chat-empty-icon">💬</div>
                                <p className="chat-empty-title">Start your mortgage journey</p>
                                <p className="chat-empty-sub">
                                    Select an option from the panel on the right, or type your question below.
                                </p>
                            </div>
                        )}

                        {messages.map((msg, i) => (
                            <div key={i} className={`chat-bubble-wrap ${msg.role}`}>
                                <div className={`chat-bubble ${msg.role}`}>
                                    <div className="chat-bubble-text">{msg.text}</div>
                                    <div className="chat-bubble-time">
                                        {new Date(msg.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                                    </div>
                                </div>
                            </div>
                        ))}

                        {thinking && (
                            <div className="chat-bubble-wrap assistant">
                                <div className="chat-bubble assistant chat-thinking">
                                    <span className="dot" /><span className="dot" /><span className="dot" />
                                </div>
                            </div>
                        )}
                        <div ref={bottomRef} />
                    </div>

                    {/* ── Input ─────────────────────────────────────────────────── */}
                    <div className="chat-input-row">
                        <textarea
                            className="chat-textarea"
                            placeholder={connected ? "Type your message…" : "Connecting…"}
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={handleKey}
                            disabled={!connected}
                            rows={1}
                        />
                        <button
                            className={`chat-send-btn ${!input.trim() || !connected ? "disabled" : ""}`}
                            onClick={handleSend}
                            disabled={!input.trim() || !connected}
                        >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                                <line x1="22" y1="2" x2="11" y2="13" />
                                <polygon points="22 2 15 22 11 13 2 9 22 2" />
                            </svg>
                        </button>
                    </div>
                    <p className="chat-hint">Press Enter to send · Shift+Enter for new line</p>
                </section>
            </main>

            <style>{`
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        .chat-root {
          display: flex; flex-direction: column; height: 100vh;
          font-family: 'Inter', -apple-system, sans-serif;
          background: #f0f4ff;
          color: #1a1a2e;
        }

        /* Header */
        .chat-header {
          background: linear-gradient(135deg, #00395d 0%, #005ea8 100%);
          padding: 0 24px; height: 64px; flex-shrink: 0;
          box-shadow: 0 2px 16px rgba(0,62,128,0.3);
        }
        .chat-header-inner {
          max-width: 1400px; margin: 0 auto; height: 100%;
          display: flex; align-items: center; justify-content: space-between;
        }
        .chat-logo { display: flex; align-items: center; gap: 10px; }
        .chat-logo-icon { font-size: 22px; }
        .chat-logo-text { font-size: 17px; font-weight: 700; color: white; letter-spacing: -0.3px; }
        .chat-badge {
          background: rgba(255,255,255,0.2); color: white;
          font-size: 11px; font-weight: 600; letter-spacing: 0.5px;
          padding: 3px 10px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.3);
        }
        .chat-status { display: flex; align-items: center; gap: 6px; font-size: 13px; color: rgba(255,255,255,0.85); }
        .chat-status-dot { width: 8px; height: 8px; border-radius: 50%; }
        .chat-status.online .chat-status-dot { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
        .chat-status.offline .chat-status-dot { background: #f87171; }

        /* Layout */
        .chat-main {
          flex: 1; display: flex; overflow: hidden;
          max-width: 1400px; width: 100%; margin: 0 auto;
          gap: 0; padding: 20px 20px 0;
        }

        /* Sidebar */
        .chat-sidebar {
          width: 340px; flex-shrink: 0;
          display: flex; flex-direction: column; gap: 10px;
          padding-right: 16px;
        }
        .chat-sidebar-label {
          font-size: 11px; font-weight: 700; letter-spacing: 1px;
          color: #64748b; text-transform: uppercase;
        }
        .chat-a2ui-panel {
          flex: 1; background: white; border-radius: 16px;
          padding: 20px; overflow-y: auto;
          box-shadow: 0 4px 24px rgba(0,62,128,0.08);
          border: 1px solid rgba(0,62,128,0.08);
        }
        .chat-a2ui-empty {
          display: flex; flex-direction: column; align-items: center;
          justify-content: center; height: 100%; gap: 12px;
          color: #94a3b8; text-align: center; padding: 24px;
        }
        .chat-a2ui-icon { font-size: 40px; opacity: 0.5; }
        .chat-a2ui-empty p { font-size: 13px; line-height: 1.6; }

        /* Chat Panel */
        .chat-panel {
          flex: 1; display: flex; flex-direction: column;
          background: white; border-radius: 16px 16px 0 0;
          box-shadow: 0 4px 24px rgba(0,62,128,0.08);
          border: 1px solid rgba(0,62,128,0.08); border-bottom: none;
          overflow: hidden; min-width: 0;
        }

        /* Messages */
        .chat-messages {
          flex: 1; overflow-y: auto; padding: 24px 20px;
          display: flex; flex-direction: column; gap: 12px;
          scroll-behavior: smooth;
        }
        .chat-messages::-webkit-scrollbar { width: 4px; }
        .chat-messages::-webkit-scrollbar-thumb { background: #e2e8f0; border-radius: 2px; }

        .chat-empty-state {
          flex: 1; display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          gap: 10px; color: #94a3b8; padding: 40px 20px; text-align: center;
        }
        .chat-empty-icon { font-size: 48px; opacity: 0.4; }
        .chat-empty-title { font-size: 16px; font-weight: 600; color: #64748b; }
        .chat-empty-sub { font-size: 13px; line-height: 1.6; max-width: 280px; }

        /* Bubbles */
        .chat-bubble-wrap {
          display: flex; align-items: flex-end; gap: 8px;
          animation: slideIn 0.2s ease;
        }
        .chat-bubble-wrap.user { flex-direction: row-reverse; }
        @keyframes slideIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }

        .chat-bubble {
          max-width: 72%; padding: 12px 16px; border-radius: 18px;
          font-size: 14px; line-height: 1.6; position: relative;
        }
        .chat-bubble.assistant {
          background: #f1f5f9; color: #1e293b;
          border-bottom-left-radius: 4px;
        }
        .chat-bubble.user {
          background: linear-gradient(135deg, #005ea8, #0070cc);
          color: white; border-bottom-right-radius: 4px;
        }
        .chat-bubble-time {
          font-size: 10px; opacity: 0.5; margin-top: 4px; text-align: right;
        }

        /* Thinking dots */
        .chat-thinking {
          display: flex; align-items: center; gap: 5px;
          padding: 14px 18px; background: #f1f5f9;
        }
        .chat-thinking .dot {
          width: 7px; height: 7px; background: #94a3b8;
          border-radius: 50%; animation: bounce 1.2s infinite;
        }
        .chat-thinking .dot:nth-child(2) { animation-delay: 0.2s; }
        .chat-thinking .dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes bounce {
          0%, 80%, 100% { transform: translateY(0); }
          40% { transform: translateY(-6px); }
        }

        /* Input */
        .chat-input-row {
          display: flex; align-items: flex-end; gap: 10px;
          padding: 16px 20px; border-top: 1px solid #f1f5f9;
          background: white;
        }
        .chat-textarea {
          flex: 1; resize: none; border: 1.5px solid #e2e8f0;
          border-radius: 12px; padding: 12px 16px;
          font-size: 14px; font-family: inherit; color: #1e293b;
          outline: none; transition: border-color 0.15s;
          line-height: 1.5; max-height: 140px; overflow-y: auto;
          background: #f8fafc;
        }
        .chat-textarea:focus { border-color: #005ea8; background: white; }
        .chat-textarea:disabled { opacity: 0.5; cursor: not-allowed; }
        .chat-send-btn {
          width: 44px; height: 44px; border-radius: 12px; border: none;
          background: linear-gradient(135deg, #005ea8, #0070cc);
          color: white; cursor: pointer; display: flex;
          align-items: center; justify-content: center;
          transition: opacity 0.15s, transform 0.1s;
          flex-shrink: 0;
        }
        .chat-send-btn:hover:not(.disabled) { opacity: 0.9; transform: scale(1.04); }
        .chat-send-btn.disabled { opacity: 0.35; cursor: not-allowed; }
        .chat-send-btn svg { width: 18px; height: 18px; }

        .chat-hint {
          text-align: center; font-size: 11px; color: #94a3b8;
          padding: 0 20px 12px;
        }
      `}</style>
        </div>
    );
}

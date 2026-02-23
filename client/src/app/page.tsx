"use client";

import React, { useState, useRef, useEffect, useMemo } from 'react';
import Image from 'next/image';
import { useMortgageSocket } from '../hooks/useMortgageSocket';
import A2Renderer, { A2UIPayload } from '../components/A2Renderer';
import LatencyHud from '../components/LatencyHud';

export default function Home() {
  const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
  const {
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
    latency
  } = useMortgageSocket(wsUrl);


  const [textInput, setTextInput] = useState('');
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Scroll to bottom of message log when new message is added
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const toggleRecording = async () => {
    if (isRecording) {
      sendAudioStop();
    } else {
      try {
        await sendAudioStart();
      } catch (err) {
        console.error("Failed to start audio", err);
      }
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      const reader = new FileReader();
      reader.onloadend = () => {
        setSelectedImage(reader.result as string);
      };
      reader.readAsDataURL(file);
    }
  };

  const submitText = (e: React.FormEvent) => {
    e.preventDefault();
    if (textInput.trim() || selectedImage) {
      sendText(textInput, selectedImage || undefined);
      setTextInput('');
      setSelectedImage(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };


  return (
    <div className="min-h-screen bg-gray-50 flex flex-col font-sans text-gray-900">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between sticky top-0 z-10 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-600 rounded-xl shadow-md flex items-center justify-center">
            <span className="text-white font-black text-lg">B</span>
          </div>
          <h1 className="font-extrabold text-2xl tracking-tight text-blue-950">Mortgage Assistant</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex bg-gray-100 rounded-lg p-1.5 shadow-inner">
            <button
              className={`px-5 py-2 rounded-md text-sm font-bold transition-all shadow-sm ${mode === 'voice' ? 'bg-white text-blue-700 ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => sendModeUpdate('voice')}
            >
              Voice Mode
            </button>
            <button
              className={`px-5 py-2 rounded-md text-sm font-bold transition-all shadow-sm ${mode === 'text' ? 'bg-white text-blue-700 ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => sendModeUpdate('text')}
            >
              Text Only
            </button>
          </div>
          {connected ? (
            <button
              onClick={disconnect}
              className="px-4 py-2 bg-red-50 text-red-600 border border-red-200 rounded-lg text-sm font-bold shadow-sm hover:bg-red-100 transition-colors whitespace-nowrap"
            >
              Disconnect
            </button>
          ) : (
            <button
              onClick={connect}
              className="px-4 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg text-sm font-bold shadow-sm hover:bg-emerald-100 transition-colors whitespace-nowrap"
            >
              Connect
            </button>
          )}
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 flex overflow-hidden">
        {/* Left Panel - Chat / Controls */}
        <div className="w-[35%] min-w-[340px] max-w-lg bg-white border-r border-gray-200 flex flex-col p-6 shadow-xl z-[5]">

          <div className="flex-1 flex flex-col justify-center gap-6">

            <div className="bg-slate-50 border border-slate-200 rounded-2xl p-6 shadow-sm flex flex-col relative overflow-hidden group hover:border-blue-200 transition-colors h-[400px]">
              {/* Decorative blur */}
              <div className="absolute top-0 w-full h-1 bg-gradient-to-r from-blue-400 to-indigo-500 left-0"></div>

              {/* Chat Message Log */}
              <div className="flex-1 overflow-y-auto w-full pr-2 space-y-4 mb-4 mt-2 no-scrollbar">
                {messages.length === 0 && !thinkingState && !voicePlaying && (
                  <div className="h-full flex items-center justify-center text-center">
                    <div className="text-gray-400 text-sm font-semibold tracking-wide">
                      {mode === 'voice' ? 'Ready for your voice command...' : 'Attach a photo or type a message below...'}
                    </div>
                  </div>
                )}
                {messages.map((msg, i) => (
                  <div key={i} className={`flex flex-col gap-1 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                    {msg.image && (
                      <Image src={msg.image} alt="User Upload" width={128} height={128} className="w-32 h-32 object-cover rounded-xl shadow-sm border border-gray-200" unoptimized />
                    )}
                    {msg.text && (
                      <div className={`rounded-2xl px-4 py-2 shadow-sm max-w-[85%] text-sm ${msg.role === 'user' ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm leading-relaxed'}`}>
                        {msg.text}
                      </div>
                    )}
                  </div>
                ))}

                {partialTranscript && (
                  <div className="flex flex-col gap-1 items-end opacity-70 italic">
                    <div className="rounded-2xl px-4 py-2 shadow-sm max-w-[85%] text-sm bg-blue-500 text-white rounded-tr-sm">
                      {partialTranscript}...
                    </div>
                  </div>
                )}

                {thinkingState && (
                  <div className="flex justify-start my-2">
                    <div className="flex bg-gray-100 rounded-2xl rounded-tl-sm px-4 py-2 gap-2 items-center border border-gray-200">
                      <div className="w-3 h-3 rounded-full border-[2px] border-blue-200 border-t-blue-600 animate-spin"></div>
                      <span className="text-xs text-gray-500 font-medium uppercase tracking-wider">{thinkingState.replace('_', ' ')}</span>
                    </div>
                  </div>
                )}

                {voicePlaying && (
                  <div className="flex justify-start my-2">
                    <div className="flex bg-white px-4 py-2 rounded-2xl rounded-tl-sm shadow-sm border border-blue-100 items-center justify-center gap-2">
                      <div className="flex items-end h-4 gap-1 justify-center">
                        <div className="w-1 bg-blue-500 rounded-full animate-pulse h-2" style={{ animationDelay: '0ms' }}></div>
                        <div className="w-1 bg-indigo-500 rounded-full animate-pulse h-4" style={{ animationDelay: '100ms' }}></div>
                        <div className="w-1 bg-blue-600 rounded-full animate-pulse h-3" style={{ animationDelay: '200ms' }}></div>
                      </div>
                      <span className="text-xs text-blue-600 font-bold uppercase tracking-widest">Assistant Speaking</span>
                    </div>
                  </div>
                )}

                <div ref={logsEndRef} />
              </div>

              {/* Controls */}
              <div className="w-full flex justify-center pt-2 border-t border-slate-200/60 relative">
                {mode === 'voice' ? (
                  <div className="relative flex items-center justify-center">
                    {/* Ring visualizer */}
                    {isRecording && (
                      <div
                        className="absolute inset-0 rounded-full bg-red-400 opacity-20 animate-ping"
                        style={{ transform: `scale(${1 + volume * 2})` }}
                      ></div>
                    )}
                    <button
                      onClick={toggleRecording}
                      className={`w-24 h-24 rounded-full flex items-center justify-center transition-all duration-200 mt-2 z-10 ${isRecording ? 'bg-red-500 scale-95 shadow-inner' : 'bg-gradient-to-br from-blue-500 to-blue-700 shadow-[0_10px_40px_-10px_rgba(37,99,235,0.7)] hover:-translate-y-2 hover:shadow-[0_20px_40px_-10px_rgba(37,99,235,0.9)]'}`}
                    >
                      <svg className="w-10 h-10 text-white fill-current" viewBox="0 0 24 24">
                        <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z" />
                        <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" />
                      </svg>
                    </button>
                    {/* Volume Bar Overlay (Simple) */}
                    {isRecording && (
                      <div className="absolute -bottom-8 w-32 h-1 bg-gray-200 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-red-500 transition-all duration-75"
                          style={{ width: `${Math.min(100, volume * 500)}%` }}
                        ></div>
                      </div>
                    )}
                  </div>

                ) : (
                  <div className="w-full flex flex-col gap-2">
                    {selectedImage && (
                      <div className="relative inline-block w-16 h-16 ml-3">
                        <Image src={selectedImage} alt="Preview" width={64} height={64} className="w-16 h-16 object-cover rounded-lg border-2 border-blue-500 shadow-sm" unoptimized />
                        <button
                          onClick={() => setSelectedImage(null)}
                          className="absolute -top-2 -right-2 bg-gray-800 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs hover:bg-red-500"
                        >
                          ×
                        </button>
                      </div>
                    )}

                    <form onSubmit={submitText} className="w-full relative flex items-center gap-2">
                      <input
                        type="file"
                        accept="image/*"
                        ref={fileInputRef}
                        className="hidden"
                        onChange={handleFileChange}
                      />
                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="p-3 text-gray-500 rounded-xl hover:bg-gray-200 transition-colors border border-gray-300 bg-white shadow-sm"
                        title="Upload property photo"
                      >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
                      </button>
                      <input
                        type="text"
                        value={textInput}
                        onChange={(e) => setTextInput(e.target.value)}
                        placeholder="Type your requirement..."
                        className="flex-1 bg-white border border-gray-300 rounded-xl pl-4 pr-24 py-3.5 text-sm font-medium focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none shadow-sm transition-shadow hover:shadow text-gray-800 placeholder-gray-400"
                      />
                      <button type="submit" className="absolute right-1.5 top-1.5 bottom-1.5 bg-blue-600 text-white px-5 rounded-lg font-bold hover:bg-blue-700 transition-colors shadow-sm focus:ring-2 focus:ring-offset-1 focus:ring-blue-500 text-sm">Send</button>
                    </form>
                  </div>
                )}
              </div>
            </div>

            <LatencyHud latency={latency} connected={connected} />
          </div>
        </div>

        {/* Right Panel - A2UI Canvas */}
        <div className="flex-1 overflow-y-auto bg-slate-100/50 flex flex-col">
          <div className="p-8 max-w-4xl mx-auto w-full flex-1 flex flex-col relative">
            {/* Decorative background circle */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-blue-50/50 rounded-full blur-3xl pointer-events-none -z-10"></div>
            <A2Renderer a2uiState={a2uiState} onAction={sendAction} />

            {/* Call an Expert FAB (Show once we have context) */}
            {a2uiState && (
              <div className="absolute bottom-10 right-10 animate-vertical-bounce transition-all hover:scale-105 active:scale-95 cursor-pointer z-50">
                <div
                  className="bg-blue-600 text-white px-6 py-4 rounded-full shadow-[0_10px_30px_rgba(30,64,175,0.4)] flex items-center gap-3 border-2 border-white"
                  onClick={() => alert("Connecting you to a Mortgage Specialist...")}
                >
                  <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center text-lg">📞</div>
                  <span className="font-black uppercase tracking-widest text-xs">Call an Expert</span>
                </div>
              </div>
            )}

            <div className="mt-auto pt-10 pb-4 text-center">
              <p className="text-[10px] font-bold text-slate-300 uppercase tracking-[0.3em]">Powered by Barclays A2UI \u2022 2026</p>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

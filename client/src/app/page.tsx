"use client";

import React, { useState, useRef, useEffect, useMemo } from 'react';
import Image from 'next/image';
import { useMortgageSocket } from '../hooks/useMortgageSocket';
import A2Renderer, { A2UIPayload } from '../components/A2Renderer';
import LatencyHud from '../components/LatencyHud';
import { langfuse } from '../lib/langfuse';

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
    sendDeviceUpdate,
    connect,
    disconnect,
    volume,
    isRecording,
    mode,
    device,
    latency
  } = useMortgageSocket(wsUrl);

  const isMobile = device === 'mobile';
  const setIsMobile = (val: boolean) => sendDeviceUpdate(val ? 'mobile' : 'desktop');


  const [textInput, setTextInput] = useState('');
  const [selectedImage, setSelectedImage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const rightPanelRef = useRef<HTMLDivElement>(null);
  const [chatY, setChatY] = useState(0);

  useEffect(() => {
    // Follow focus logic: find the element being highlighted in the right panel
    // and slide the chat box to be vertically aligned with it.
    const updateChatPosition = () => {
      if (isMobile) {
        setChatY(0);
        return;
      }
      const focusedEl = document.querySelector('[data-a2-focused="true"]');
      const panel = rightPanelRef.current;
      if (focusedEl && panel) {
        const focusedRect = focusedEl.getBoundingClientRect();
        const panelRect = panel.getBoundingClientRect();

        // Match the element's top position in the viewport, 
        // offset by a small bit to look balanced.
        const chatHeight = 400; // Expected max height
        let targetY = focusedRect.top - panelRect.top;

        // Constrain to panel visible area 
        const maxScroll = panelRect.height - chatHeight - 40;
        targetY = Math.max(20, Math.min(maxScroll, targetY));

        setChatY(targetY);
      } else {
        setChatY(0);
      }
    };

    // Use ResizeObserver to detect when the content on the right changes height
    const panel = rightPanelRef.current;
    if (!panel) return;

    const observer = new ResizeObserver(updateChatPosition);
    observer.observe(panel);

    // Also update on scroll and manual window resize
    panel.addEventListener('scroll', updateChatPosition);
    window.addEventListener('resize', updateChatPosition);

    // Initial positioning
    const timer = setTimeout(updateChatPosition, 100);

    return () => {
      clearTimeout(timer);
      observer.disconnect();
      panel.removeEventListener('scroll', updateChatPosition);
      window.removeEventListener('resize', updateChatPosition);
    };
  }, [a2uiState, messages]);

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
      if (langfuse) {
        langfuse.trace({
          name: "user_message",
          metadata: { text: textInput, hasImage: !!selectedImage }
        });
      }
      sendText(textInput, selectedImage || undefined);
      setTextInput('');
      setSelectedImage(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const renderChatLog = (isMobileView = false) => (
    <div className={`flex-1 overflow-y-auto w-full pr-2 space-y-4 mb-4 mt-2 no-scrollbar ${isMobileView ? 'px-4' : ''}`}>
      {messages.length === 0 && !thinkingState && !voicePlaying && !isMobileView && (
        <div className="h-full flex items-center justify-center text-center">
          <div className="text-gray-400 text-[11px] font-bold uppercase tracking-widest opacity-60">
            {mode === 'voice' ? 'Awaiting Voice...' : 'Send Message...'}
          </div>
        </div>
      )}
      {messages.map((msg, i) => (
        <div key={i} className={`flex flex-col gap-1 ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
          {msg.image && (
            <Image src={msg.image} alt="User Upload" width={128} height={128} className="w-32 h-32 object-cover rounded-xl shadow-sm border border-gray-200" unoptimized />
          )}
          {msg.text && (
            <div className={`rounded-2xl ${isMobileView ? 'px-3 py-1.5' : 'px-4 py-2'} shadow-sm max-w-[85%] text-sm ${msg.role === 'user' ? 'bg-blue-600 text-white rounded-tr-sm' : 'bg-white border border-gray-200 text-gray-800 rounded-tl-sm leading-relaxed'}`}>
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
            <span className="text-[10px] text-gray-500 font-bold uppercase tracking-wider">{thinkingState.replace('_', ' ')}</span>
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
            <span className="text-[10px] text-blue-600 font-black uppercase tracking-widest">Live Audio</span>
          </div>
        </div>
      )}
      <div ref={logsEndRef} />
    </div>
  );

  const renderChatControls = (compact = false) => (
    <div className={`w-full flex justify-center pt-2 ${compact ? '' : 'border-t border-slate-200/60 transition-all focus-within:border-blue-300'} relative`}>
      {mode === 'voice' ? (
        <div className="relative flex items-center justify-center py-2">
          {/* Ring visualizer */}
          {isRecording && (
            <div
              className="absolute inset-0 rounded-full bg-red-400 opacity-20 animate-ping"
              style={{ transform: `scale(${1 + volume * 2})` }}
            ></div>
          )}
          <button
            onClick={toggleRecording}
            className={`${compact ? 'w-16 h-16' : 'w-24 h-24'} rounded-full flex items-center justify-center transition-all duration-200 z-10 ${isRecording ? 'bg-red-500 scale-95 shadow-inner' : 'bg-gradient-to-br from-blue-500 to-blue-700 shadow-[0_10px_40px_-10px_rgba(37,99,235,0.7)] hover:-translate-y-1 hover:shadow-[0_15px_35px_-10px_rgba(37,99,235,0.9)]'}`}
          >
            <svg className={`${compact ? 'w-6 h-6' : 'w-10 h-10'} text-white fill-current`} viewBox="0 0 24 24">
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z" />
              <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" />
            </svg>
          </button>
          {isRecording && (
            <div className={`absolute ${compact ? '-bottom-4' : '-bottom-6'} w-32 h-1 bg-gray-200 rounded-full overflow-hidden`}>
              <div
                className="h-full bg-red-500 transition-all duration-75"
                style={{ width: `${Math.min(100, volume * 500)}%` }}
              ></div>
            </div>
          )}
        </div>
      ) : (
        <div className="w-full flex flex-col gap-3">
          {selectedImage && (
            <div className="relative inline-block w-16 h-16 ml-3">
              <Image src={selectedImage} alt="Preview" width={64} height={64} className="w-16 h-16 object-cover rounded-lg border-2 border-blue-500 shadow-md" unoptimized />
              <button
                onClick={() => setSelectedImage(null)}
                className="absolute -top-2 -right-2 bg-red-500 text-white rounded-full w-5 h-5 flex items-center justify-center text-xs hover:bg-black transition-colors shadow-sm"
              >
                ×
              </button>
            </div>
          )}

          <form onSubmit={submitText} className="w-full relative flex items-center gap-2">
            <input type="file" accept="image/*" ref={fileInputRef} className="hidden" onChange={handleFileChange} />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className={`${compact ? 'p-2' : 'p-3'} text-gray-500 rounded-xl hover:bg-gray-100 transition-colors border border-gray-200 bg-white shadow-sm flex-shrink-0`}
              title="Upload photo"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" /></svg>
            </button>
            <input
              type="text"
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              placeholder={compact ? "Type message..." : "How can I help you today?"}
              className={`flex-1 bg-white border border-gray-200 rounded-xl ${compact ? 'pl-3 pr-16 py-2' : 'pl-4 pr-24 py-3.5'} text-sm font-medium focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none shadow-sm transition-all text-gray-800 placeholder-gray-400`}
            />
            <button
              type="submit"
              className={`absolute right-1 top-1 bottom-1 bg-blue-600 text-white ${compact ? 'px-3' : 'px-5'} rounded-lg font-bold hover:bg-blue-700 transition-colors shadow-sm text-sm`}
            >
              {compact ? '→' : 'Send'}
            </button>
          </form>
        </div>
      )}
    </div>
  );



  return (
    <div className={`min-h-screen bg-gray-50 flex flex-col font-sans text-gray-900 ${isMobile ? 'overflow-hidden' : ''}`}>
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between sticky top-0 z-20 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-blue-600 rounded-xl shadow-md flex items-center justify-center">
            <span className="text-white font-black text-lg">B</span>
          </div>
          <h1 className="font-extrabold text-2xl tracking-tight text-blue-950">Mortgage Assistant</h1>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex bg-gray-100 rounded-lg p-1.5 shadow-inner">
            <button
              className={`px-4 py-2 rounded-md text-xs font-bold transition-all flex items-center gap-2 ${!isMobile ? 'bg-white text-blue-700 shadow-sm ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => setIsMobile(false)}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" /></svg>
              Desktop
            </button>
            <button
              className={`px-4 py-2 rounded-md text-xs font-bold transition-all flex items-center gap-2 ${isMobile ? 'bg-white text-blue-700 shadow-sm ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => setIsMobile(true)}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" /></svg>
              Mobile View
            </button>
          </div>

          <div className="w-px h-6 bg-gray-200 mx-1"></div>

          <div className="flex bg-gray-100 rounded-lg p-1.5 shadow-inner">
            <button
              className={`px-5 py-2 rounded-md text-sm font-bold transition-all shadow-sm ${mode === 'voice' ? 'bg-white text-blue-700 ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => sendModeUpdate('voice')}
            >
              Voice
            </button>
            <button
              className={`px-5 py-2 rounded-md text-sm font-bold transition-all shadow-sm ${mode === 'text' ? 'bg-white text-blue-700 ring-1 ring-gray-200' : 'text-gray-500 hover:text-gray-900 hover:bg-gray-200/50'}`}
              onClick={() => sendModeUpdate('text')}
            >
              Text
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
      <main className="flex-1 flex overflow-hidden relative">
        {!isMobile ? (
          <>
            {/* Left Panel - Chat / Controls */}
            <div className="w-[35%] min-w-[340px] max-w-lg bg-white border-r border-gray-200 p-6 shadow-xl z-[5] overflow-y-auto no-scrollbar relative">
              <div
                className="flex flex-col gap-6 transition-all duration-700 ease-in-out absolute left-6 right-6"
                style={{ top: `${chatY + 24}px` }}
              >
                <div className="bg-slate-50 border border-slate-200 rounded-2xl p-6 shadow-sm flex flex-col relative overflow-hidden group hover:border-blue-200 transition-colors h-[410px]">
                  {/* Decorative line */}
                  <div className="absolute top-0 w-full h-1 bg-gradient-to-r from-blue-400 to-indigo-500 left-0"></div>

                  {/* Chat Message Log */}
                  {renderChatLog()}

                  {/* Controls */}
                  {renderChatControls()}
                </div>

                <LatencyHud latency={latency} connected={connected} />
              </div>
            </div>

            {/* Right Panel - A2UI Canvas */}
            <div ref={rightPanelRef} className="flex-1 overflow-y-auto bg-slate-100/30 flex flex-col scroll-smooth">
              <div className="p-8 max-w-4xl mx-auto w-full flex-1 flex flex-col relative">
                {/* Decorative background circle */}
                <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-blue-100/20 rounded-full blur-[100px] pointer-events-none -z-10"></div>
                <A2Renderer a2uiState={a2uiState} onAction={sendAction} isMobile={isMobile} />

                {/* Speak to a Colleague FAB */}
                {a2uiState?.showSupport && (
                  <div className="fixed bottom-10 right-10 animate-bounce transition-all hover:scale-110 active:scale-95 cursor-pointer z-[100]">
                    <div
                      className="bg-blue-600 text-white px-6 py-4 rounded-full shadow-[0_20px_40px_rgba(30,64,175,0.4)] flex items-center gap-3 border-2 border-white/20 backdrop-blur-sm"
                      onClick={() => alert("Connecting you to a Mortgage Specialist...")}
                    >
                      <div className="w-8 h-8 bg-blue-500/50 rounded-full flex items-center justify-center text-lg">📞</div>
                      <span className="font-black uppercase tracking-widest text-xs">Speak to a Colleague</span>
                    </div>
                  </div>
                )}

                <div className="mt-auto pt-10 pb-4 text-center">
                  <p className="text-[10px] font-black text-slate-300 uppercase tracking-[0.4em]">Powered by Barclays A2UI • 2026</p>
                </div>
              </div>
            </div>
          </>
        ) : (
          /* Mobile View - iPhone Frame */
          <div className="flex-1 bg-slate-900 flex items-center justify-center p-4 relative overflow-hidden">
            {/* Background elements for mobile mode */}
            <div className="absolute top-0 left-0 w-full h-full opacity-30 pointer-events-none overflow-hidden">
              <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-600 rounded-full blur-[120px]"></div>
              <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-indigo-600 rounded-full blur-[120px]"></div>
            </div>

            {/* iPhone Frame */}
            <div className="relative w-[340px] h-[680px] bg-black rounded-[48px] shadow-[0_0_0_10px_#1a1a1b,0_0_0_12px_#333,0_30px_60px_-15px_rgba(0,0,0,0.8)] flex flex-col overflow-hidden ring-1 ring-white/10 group">
              {/* Screen Content Wrapper */}
              <div className="absolute inset-[3px] bg-white rounded-[45px] flex flex-col overflow-hidden box-border border-[6px] border-black">

                {/* iPhone Dynamic Island / Notch */}
                <div className="absolute top-0 left-1/2 -translate-x-1/2 w-32 h-8 bg-black rounded-b-[20px] z-[100] flex items-center justify-center">
                  <div className="w-12 h-1 bg-zinc-800 rounded-full mb-1"></div>
                </div>

                {/* Status Bar simulation */}
                <div className="h-10 px-8 flex items-center justify-between text-[11px] font-black text-black z-50 pt-1">
                  <span>9:41</span>
                  <div className="flex items-center gap-1.5">
                    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M12.01 21.49L23.64 7c-.45-.34-4.93-4-11.64-4C5.28 3 .81 6.65.36 7l11.63 14.49.01.01.01-.01z" fillOpacity=".3" /><path d="M3.53 10.95L12 21.5l8.47-10.55C20.04 10.62 16.81 8 12 8s-8.04 2.62-8.47 2.95z" /></svg>
                    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M15.67 4H14V2h-4v2H8.33C7.6 4 7 4.6 7 5.33v15.33C7 21.4 7.6 22 8.33 22h7.33c.74 0 1.34-.6 1.34-1.34V5.33C17 4.6 16.4 4 15.67 4z" /></svg>
                  </div>
                </div>

                {/* Mobile Scrollable Area */}
                <div className="flex-1 overflow-y-auto flex flex-col relative no-scrollbar">
                  {/* App Header */}
                  <div className="px-6 pt-4 pb-4 border-b border-gray-100 flex items-center justify-between sticky top-0 bg-white/80 backdrop-blur-md z-[60]">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-black text-sm">B</div>
                      <div>
                        <h2 className="text-sm font-black text-blue-950 leading-none">Mortgage</h2>
                        <p className="text-[10px] text-gray-400 font-bold uppercase tracking-wider mt-0.5">Assistant AI</p>
                      </div>
                    </div>
                    {/* Barclays Eagle Logo for existing customers */}
                    {a2uiState?.isExistingCustomer && (
                      <div className="w-8 h-8 flex items-center justify-center animate-in fade-in zoom-in duration-500">
                        <img
                          src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAMAAABF0y+mAAAAM1BMVEVHcEwAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+kAr+lRZTNJAAAAEXRSTlMAPIejVCr/ZK/Yyue/95UZeLE8H+YAAACjSURBVHgBrc5bisQgFEXRY9RtfGf+o23aRJAuG4qi1u/lbK6+whzWac/zK2jLmwM49eKIyUjKUCSFmJZABJBUIT/9pkfg7hkAPwLQ12FulqG1uk7ZyRo8WxrMx0fP1hsPKbNx6tbYcLp1IFIig61EQFPhKnhu0XElLk09iSvw6KkqamGLmJKq08rphCnISCvDQn8Fpur1oicGqy2TwHb9p+t9P78sCiCxm+C+AAAAAElFTkSuQmCC"
                          alt="Barclays Eagle"
                          className="w-6 h-6 object-contain"
                        />
                      </div>
                    )}
                  </div>

                  {/* Main A2UI Content */}
                  <div className="px-4 py-2 flex flex-col flex-1 min-h-0 bg-slate-50/50">
                    <div className="flex-1">
                      <A2Renderer a2uiState={a2uiState} onAction={sendAction} isMobile={isMobile} />
                    </div>
                    <div className="h-10 flex-shrink-0"></div> {/* Reduced spacer for bottom shadow/chat */}
                  </div>

                  {/* Floating Support Button in Mobile */}
                  {a2uiState?.showSupport && (
                    <div className="px-4 py-3 sticky bottom-4 z-50">
                      <button
                        onClick={() => alert("Connecting you to a Mortgage Specialist...")}
                        className="w-full bg-blue-600 text-white rounded-2xl py-3 px-4 shadow-xl border-2 border-blue-400/30 flex items-center justify-center gap-3 active:scale-95 transition-transform"
                      >
                        <span className="text-lg">📞</span>
                        <span className="font-black uppercase tracking-widest text-[10px]">Speak to Specialist</span>
                      </button>
                    </div>
                  )}
                </div>

                {/* Mobile Bottom Chat Component */}
                <div className="bg-white border-t border-gray-100 p-3 pb-6 shadow-[0_-10px_30px_rgba(0,0,0,0.05)] z-50">
                  <div className="max-h-[160px] flex flex-col overflow-hidden mb-2">
                    {renderChatLog(true)}
                  </div>
                  {renderChatControls(true)}

                  {/* iPhone Home Indicator */}
                  <div className="w-32 h-1.5 bg-black/10 rounded-full mx-auto mt-4"></div>
                </div>
              </div>

              {/* Volume Buttons / Power Button side elements */}
              <div className="absolute top-24 left-[-2px] w-[2px] h-12 bg-[#333] rounded-r-sm"></div>
              <div className="absolute top-40 left-[-2px] w-[2px] h-16 bg-[#333] rounded-r-sm"></div>
              <div className="absolute top-60 left-[-2px] w-[2px] h-16 bg-[#333] rounded-r-sm"></div>
              <div className="absolute top-36 right-[-2px] w-[2px] h-24 bg-[#333] rounded-l-sm"></div>
            </div>

            {/* Hint for mobile mode */}
            <div className="absolute bottom-8 text-slate-500 font-bold uppercase tracking-widest text-[10px] bg-white/5 px-4 py-2 rounded-full backdrop-blur-sm">
              Mobile Interaction Simulation
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

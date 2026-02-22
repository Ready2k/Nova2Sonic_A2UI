import React from 'react';

export default function LatencyHud({ latency, connected }: { latency: any, connected: boolean }) {
    return (
        <div className="text-xs font-mono space-y-1 mt-4 p-3 bg-gray-900 rounded text-green-400 border border-gray-700 shadow-inner">
            <div className={`flex items-center gap-2 mb-2 pb-2 border-b border-gray-700 ${connected ? 'text-green-500' : 'text-red-500'}`}>
                <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}></div>
                <span className="font-bold">{connected ? 'WS CONNECTED' : 'WS DISCONNECTED'}</span>
            </div>
            <div className="flex justify-between">
                <span className="text-gray-400">TTFB:</span>
                <span>{latency.ttfb ? `${latency.ttfb}ms` : '--'}</span>
            </div>
            <div className="flex justify-between">
                <span className="text-gray-400">UI Patch:</span>
                <span className={latency.uiPatchLatency && latency.uiPatchLatency < 150 ? 'text-blue-400' : ''}>{latency.uiPatchLatency ? `${latency.uiPatchLatency}ms` : '--'}</span>
            </div>
            <div className="flex justify-between">
                <span className="text-gray-400">Voice Start:</span>
                <span>{latency.voiceLatency ? `${latency.voiceLatency}ms` : '--'}</span>
            </div>
        </div>
    );
}

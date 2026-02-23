import React from 'react';

export interface A2UIComponent {
    id: string;
    component: string;
    children?: string[];
    text?: string;
    variant?: string;
    value?: number;
    max?: number;
    data?: Record<string, unknown>;
    focus?: boolean;
}

export interface A2UIUpdateComponents {
    surfaceId: string;
    components: A2UIComponent[];
}

export interface A2UIPayload {
    version: string;
    showSupport?: boolean;
    updateComponents: A2UIUpdateComponents;
}

interface A2RendererProps {
    a2uiState: A2UIPayload | null;
    onAction: (id: string, data?: Record<string, unknown>) => void;
}

const A2Renderer: React.FC<A2RendererProps> = ({ a2uiState, onAction }) => {
    if (!a2uiState || !a2uiState.updateComponents) {
        return (
            <div className="h-full flex flex-col items-center justify-center p-8 text-center">
                <div className="w-20 h-20 bg-blue-50 rounded-full flex items-center justify-center mb-6 animate-pulse">
                    <svg className="w-10 h-10 text-blue-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9l-.707.707M16.243 4.243l-.707.707" /></svg>
                </div>
                <p className="text-gray-400 font-bold uppercase tracking-widest text-xs">Waiting for Analysis</p>
                <p className="text-gray-300 text-sm mt-1">Speak to the assistant to generate a report</p>
            </div>
        );
    }

    const { components } = a2uiState.updateComponents;
    const componentMap = new Map<string, A2UIComponent>();
    components.forEach((c) => componentMap.set(c.id, c));

    const renderComponent = (id: string): React.ReactNode => {
        const component = componentMap.get(id);
        if (!component) return null;

        const children = component.children?.map((childId) => renderComponent(childId));

        switch (component.component) {
            case 'Column':
                return (
                    <div key={id} className="flex flex-col gap-6 w-full animate-in fade-in slide-in-from-bottom-4 duration-700">
                        {children}
                    </div>
                );
            case 'Row':
                return (
                    <div key={id} className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full">
                        {children}
                    </div>
                );
            case 'Text': {
                const baseVariant = {
                    h1: 'text-3xl font-black tracking-tight',
                    h2: 'text-2xl font-black tracking-tight border-b border-blue-100 pb-2 mb-2',
                    h3: 'text-xl font-bold',
                    body: 'text-sm leading-relaxed',
                }[component.variant || 'body'];
                const focusClasses = component.focus
                    ? 'text-blue-600 animate-pulse'
                    : component.variant === 'h1' || component.variant === 'h2'
                        ? 'text-blue-950'
                        : component.variant === 'h3'
                            ? 'text-gray-900'
                            : 'text-gray-600';
                return (
                    <p key={id} className={`${baseVariant} ${focusClasses} transition-colors duration-300`}>
                        {component.text}
                    </p>
                );
            }
            case 'Gauge':
                const ltv = component.value || 0;
                return (
                    <div key={id} className="p-8 bg-white rounded-3xl shadow-[0_20px_50px_rgba(8,_112,_184,_0.1)] border border-blue-50 flex flex-col items-center">
                        <div className="relative w-56 h-28 overflow-hidden">
                            <div className="absolute top-0 left-0 w-56 h-56 border-[20px] border-slate-50 rounded-full"></div>
                            <div
                                className="absolute top-0 left-0 w-56 h-56 border-[20px] border-blue-600 rounded-full transition-all duration-1000 ease-out"
                                style={{
                                    clipPath: `polygon(0 50%, 100% 50%, 100% 100%, 0 100%)`,
                                    transform: `rotate(${(ltv / (component.max || 100)) * 180 - 180}deg)`
                                }}
                            ></div>
                            <div className="absolute bottom-0 left-0 w-full text-center">
                                <span className="text-4xl font-black text-blue-950">{ltv}%</span>
                                <p className="text-[10px] font-extrabold text-blue-400 uppercase tracking-[0.2em] mt-1">Loan to Value</p>
                            </div>
                        </div>
                        {ltv > 0 && <div className="mt-8 flex gap-2">
                            <span className={`text-[10px] font-black uppercase tracking-widest px-4 py-1.5 rounded-full border shadow-sm ${ltv > 80 ? 'bg-red-50 text-red-700 border-red-100' :
                                ltv < 60 ? 'bg-green-50 text-green-700 border-green-100' :
                                    'bg-blue-50 text-blue-700 border-blue-100'
                                }`}>
                                {ltv > 80 ? 'High LTV' : ltv < 60 ? 'Low LTV' : 'Tier 2 LTV'}
                            </span>
                            <span className="text-[10px] font-black uppercase tracking-widest px-4 py-1.5 bg-slate-50 text-slate-500 rounded-full border border-slate-100 shadow-sm">
                                Verified
                            </span>
                        </div>}
                    </div>
                );
            case 'ProductCard':
                const p = (component.data ?? {}) as { id?: string; name?: string; rate?: number; fee?: number; monthlyPayment?: number; totalInterest?: number };
                return (
                    <div
                        key={id}
                        className="border-2 border-transparent bg-white shadow-xl p-8 rounded-3xl hover:border-blue-500 hover:shadow-2xl cursor-pointer transition-all flex flex-col justify-between group relative overflow-hidden active:scale-[0.98]"
                        onClick={() => onAction('select_product', { productId: p.id })}
                    >
                        {/* Background design */}
                        <div className="absolute -right-4 -top-4 w-24 h-24 bg-blue-50/50 rounded-full blur-2xl group-hover:bg-blue-100/50 transition-colors"></div>

                        <div className="relative z-10">
                            <div className="flex justify-between items-start">
                                <h3 className="font-black text-xl text-blue-950 group-hover:text-blue-700 transition-colors leading-tight">{p.name}</h3>
                                <div className="bg-blue-600 text-white text-[10px] font-black px-2 py-1 rounded-md uppercase">Top Rate</div>
                            </div>
                            <div className="flex gap-4 mt-4 text-[10px] font-black text-slate-500 tracking-widest uppercase">
                                <div className="flex items-center gap-1.5">
                                    <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                                    RATE: {p.rate}%
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                                    FEE: £{p.fee}
                                </div>
                            </div>
                        </div>
                        <div className="mt-8 pt-6 border-t border-slate-50 flex items-center justify-between relative z-10">
                            <div>
                                <div className="flex items-baseline gap-1">
                                    <span className="text-4xl font-black text-blue-950">£{p.monthlyPayment}</span>
                                    <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">/ mo</span>
                                </div>
                                <p className="text-[10px] font-bold text-slate-400 mt-2 uppercase tracking-tight">Est. Total Interest: £{p.totalInterest?.toLocaleString()}</p>
                            </div>
                            <div className="w-12 h-12 rounded-2xl bg-slate-50 flex items-center justify-center text-blue-600 group-hover:bg-blue-600 group-hover:text-white group-hover:-translate-y-1 transition-all shadow-sm">
                                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M9 5l7 7-7 7" /></svg>
                            </div>
                        </div>
                    </div>
                );
            case 'Button':
                return (
                    <button
                        key={id}
                        className="w-full bg-blue-600 text-white px-6 py-4 rounded-2xl font-black uppercase tracking-widest text-sm hover:bg-blue-700 transition-all shadow-lg active:scale-[0.98] mt-4"
                        onClick={() => {
                            if (component.data?.url) {
                                window.open(String(component.data.url), '_blank');
                            } else {
                                onAction(id, component.data);
                            }
                        }}
                    >
                        {component.text}
                    </button>
                );
            case 'Map': {
                const mapData = (component.data || {}) as { lat?: number; lng?: number; address?: string };
                const lat = mapData.lat || 51.5074;
                const lng = mapData.lng || -0.1278;
                const address = mapData.address || '';

                const leafletHtml = `
                <html>
                  <head>
                    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
                    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
                    <style>
                      html, body { height: 100%; margin: 0; padding: 0; }
                      #map { height: 100%; width: 100%; }
                    </style>
                  </head>
                  <body>
                    <div id="map" style="height: 100%; width: 100%; background: #f8fafc; display: flex; align-items: center; justify-content: center;">
                        <div style="color: #94a3b8; font-family: sans-serif; font-size: 12px;">Loading Map...</div>
                    </div>
                    <script>
                      window.onload = function() {
                        try {
                          if (typeof L === 'undefined') {
                            document.getElementById('map').innerHTML = '<div style="color: #ef4444; font-family: sans-serif; font-size: 11px;">Map Library Failed to Load</div>';
                            return;
                          }
                          const map = L.map('map', { zoomControl: false }).setView([${lat}, ${lng}], 15);
                          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                            attribution: '&copy; OpenStreetMap'
                          }).addTo(map);
                          L.marker([${lat}, ${lng}]).addTo(map)
                            .bindPopup('${address.replace(/'/g, "\\'")}').openPopup();
                          
                          // Force a resize check after a small delay to ensure rendering matches container
                          setTimeout(() => { map.invalidateSize(); }, 200);
                        } catch (e) {
                          console.error(e);
                          document.getElementById('map').innerHTML = '<div style="color: #ef4444; font-family: sans-serif; font-size: 11px;">Map Load Error</div>';
                        }
                      };
                    </script>
                  </body>
                </html>
                `;

                return (
                    <div key={id} className="w-full h-72 rounded-3xl overflow-hidden shadow-xl border-4 border-white mb-6 animate-in zoom-in-95 duration-700">
                        <iframe
                            width="100%"
                            height="100%"
                            frameBorder="0"
                            style={{ border: 0 }}
                            srcDoc={leafletHtml}
                            title="Property Map"
                        ></iframe>
                    </div>
                );
            }
            case 'Timeline': {
                const steps = (component.data?.steps as string[]) || [];
                const current = (component.data?.current as number) || 0;
                return (
                    <div key={id} className="w-full flex justify-between items-center mb-10 px-4 relative">
                        <div className="absolute top-1/2 left-0 w-full h-0.5 bg-slate-100 -z-10 -translate-y-1/2"></div>
                        {steps.map((step, i) => (
                            <div key={i} className="flex flex-col items-center gap-2">
                                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-[10px] font-black transition-all duration-500 shadow-sm ${i < current ? 'bg-green-500 text-white' :
                                    i === current ? 'bg-blue-600 text-white ring-4 ring-blue-100 scale-110' :
                                        'bg-white text-slate-300 border-2 border-slate-100'
                                    }`}>
                                    {i < current ? '✓' : i + 1}
                                </div>
                                <span className={`text-[10px] font-black uppercase tracking-tighter ${i === current ? 'text-blue-600' : 'text-slate-400'}`}>{step}</span>
                            </div>
                        ))}
                    </div>
                );
            }
            case 'DataCard': {
                const items = (component.data?.items as { label: string, value: string, icon?: string }[]) || [];
                return (
                    <div key={id} className="grid grid-cols-2 gap-4 mb-6">
                        {items.map((item, i) => (
                            <div key={i} className="bg-white p-4 rounded-2xl shadow-sm border border-slate-50 flex items-center gap-3 group hover:border-blue-100 transition-colors">
                                <div className="w-10 h-10 rounded-xl bg-slate-50 flex items-center justify-center text-blue-600 group-hover:bg-blue-50 transition-colors">
                                    {item.label.includes('Energy') ? '⚡' : item.label.includes('Tax') ? '🏛️' : '📋'}
                                </div>
                                <div>
                                    <p className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{item.label}</p>
                                    <p className="text-sm font-black text-blue-950">{item.value}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                );
            }
            case 'BenefitCard': {
                return (
                    <div key={id} className="w-full bg-gradient-to-br from-green-50 to-emerald-50 p-6 rounded-3xl border border-green-100 mb-6 flex items-center gap-5 animate-in slide-in-from-right-8 duration-1000">
                        <div className="w-14 h-14 bg-green-500 rounded-2xl flex items-center justify-center text-2xl shadow-lg shadow-green-200/50">
                            🌿
                        </div>
                        <div>
                            <p className="text-[10px] font-black text-green-700 uppercase tracking-widest mb-1">{component.variant || 'Reward'}</p>
                            <h4 className="text-lg font-black text-blue-950 leading-tight">{component.text}</h4>
                            <p className="text-xs text-green-800/70 font-medium mt-1">{component.data?.detail as string}</p>
                        </div>
                    </div>
                );
            }
            case 'ComparisonBadge': {
                return (
                    <div key={id} className="inline-flex items-center gap-2 bg-blue-50 px-3 py-1.5 rounded-xl border border-blue-100 mb-2">
                        <span className="text-blue-600 text-xs">📈</span>
                        <span className="text-[10px] font-bold text-blue-800 uppercase tracking-tight">{component.text}</span>
                    </div>
                );
            }
            case 'Image':
                return (
                    <div key={id} className="flex justify-center p-4">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={(component.data?.url as string) || undefined}
                            alt={component.text}
                            className="max-w-[120px] h-auto object-contain animate-in zoom-in-50 duration-500"
                        />
                    </div>
                );
            default:
                return <div key={id} className="p-4 bg-red-50 text-red-500 rounded-xl border border-red-100 text-xs font-bold">MISSING COMPONENT: {component.component}</div>;
        }
    };

    return (
        <div className="w-full">
            {renderComponent('root')}
        </div>
    );
};

export default A2Renderer;

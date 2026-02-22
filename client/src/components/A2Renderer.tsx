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
            case 'Image':
                return (
                    <div key={id} className="flex justify-center p-4">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            src={component.data?.url}
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

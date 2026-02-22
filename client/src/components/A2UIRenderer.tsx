import React from 'react';

export interface A2UIComponent {
    id: string;
    component: string;
    children?: string[];
    text?: string;
    variant?: string;
    value?: number;
    max?: number;
    data?: any;
}

export interface A2UIUpdateComponents {
    surfaceId: string;
    components: A2UIComponent[];
}

export interface A2UIPayload {
    version: string;
    updateComponents: A2UIUpdateComponents;
}

interface A2UIRendererProps {
    payload: A2UIPayload | null;
    onAction: (id: string, data?: any) => void;
}

const A2UIRenderer: React.FC<A2UIRendererProps> = ({ payload, onAction }) => {
    if (!payload || !payload.updateComponents) {
        return <div className="flex h-full items-center justify-center p-8 text-gray-400">Awaiting guidance...</div>;
    }

    const { components } = payload.updateComponents;
    const componentMap = new Map<string, A2UIComponent>();
    components.forEach((c) => componentMap.set(c.id, c));

    const renderComponent = (id: string): React.ReactNode => {
        const component = componentMap.get(id);
        if (!component) return null;

        const children = component.children?.map((childId) => renderComponent(childId));

        switch (component.component) {
            case 'Column':
                return (
                    <div key={id} className="flex flex-col gap-6 w-full">
                        {children}
                    </div>
                );
            case 'Row':
                return (
                    <div key={id} className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full">
                        {children}
                    </div>
                );
            case 'Text':
                const variantClasses = {
                    h1: 'text-3xl font-black text-blue-950 tracking-tight',
                    h2: 'text-2xl font-black text-blue-950 tracking-tight',
                    h3: 'text-xl font-bold text-gray-900',
                    body: 'text-sm text-gray-600 leading-relaxed',
                }[component.variant || 'body'];
                return (
                    <p key={id} className={variantClasses}>
                        {component.text}
                    </p>
                );
            case 'Gauge':
                const ltv = component.value || 0;
                return (
                    <div key={id} className="p-6 bg-white rounded-xl shadow-lg border border-gray-100 flex flex-col items-center">
                        <div className="relative w-48 h-24 overflow-hidden">
                            <div className="absolute top-0 left-0 w-48 h-48 border-[16px] border-gray-100 rounded-full"></div>
                            <div
                                className="absolute top-0 left-0 w-48 h-48 border-[16px] border-blue-600 rounded-full transition-all duration-1000 ease-out"
                                style={{
                                    clipPath: `polygon(0 50%, 100% 50%, 100% 100%, 0 100%)`,
                                    transform: `rotate(${(ltv / (component.max || 100)) * 180 - 180}deg)`
                                }}
                            ></div>
                            <div className="absolute bottom-0 left-0 w-full text-center">
                                <span className="text-3xl font-black text-blue-950">{ltv}%</span>
                                <p className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">Loan to Value</p>
                            </div>
                        </div>
                        {ltv > 0 && <span className="mt-4 text-xs font-bold uppercase tracking-widest px-3 py-1 bg-blue-50 text-blue-700 rounded-full border border-blue-100">
                            {ltv > 80 ? 'High Risk' : ltv < 60 ? 'Low Risk' : 'Standard Risk'}
                        </span>}
                    </div>
                );
            case 'ProductCard':
                const p = component.data;
                return (
                    <div
                        key={id}
                        className="border-2 border-transparent bg-white shadow-md p-6 rounded-xl hover:border-blue-500 hover:shadow-lg cursor-pointer transition-all flex flex-col justify-between group"
                        onClick={() => onAction('select_product', { productId: p.id })}
                    >
                        <div>
                            <h3 className="font-bold text-lg text-gray-900 group-hover:text-blue-700 transition-colors uppercase tracking-tight">{p.name}</h3>
                            <div className="flex gap-3 mt-3 text-xs font-bold text-gray-500 bg-gray-50 inline-flex px-3 py-1.5 rounded-lg border border-gray-100">
                                <span>RATE: {p.rate}%</span>
                                <span className="text-gray-300">|</span>
                                <span>FEE: £{p.fee}</span>
                            </div>
                        </div>
                        <div className="mt-6 pt-4 border-t border-gray-100 flex items-center justify-between">
                            <div>
                                <div className="flex items-baseline gap-1">
                                    <span className="text-3xl font-black text-blue-900">£{p.monthlyPayment}</span>
                                    <span className="text-xs font-bold text-gray-400 uppercase">/ mo</span>
                                </div>
                                <p className="text-[10px] font-bold text-gray-400 mt-1 uppercase tracking-tighter">Total Interest: £{p.totalInterest?.toLocaleString()}</p>
                            </div>
                            <div className="w-8 h-8 rounded-full bg-blue-50 flex items-center justify-center text-blue-600 group-hover:bg-blue-600 group-hover:text-white transition-colors shadow-sm">
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M9 5l7 7-7 7" /></svg>
                            </div>
                        </div>
                    </div>
                );
            default:
                return <div key={id}>Unknown component: {component.component}</div>;
        }
    };

    return (
        <div className="w-full">
            {renderComponent('root')}
        </div>
    );
};

export default A2UIRenderer;

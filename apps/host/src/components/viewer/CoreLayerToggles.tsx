// =============================================================================
// Core Layer Toggles - Built-in widget for the layer manager
// =============================================================================
// Layer toggle controls for the unified viewer.
// This is a "core" widget that's always available, not loaded from a module.

import React from 'react';
import { useViewer, LayerType } from '@/context/ViewerContext';
import { useI18n } from '@/context/I18nContext';
import {
    MapPin,
    Bot,
    Gauge,
    Cloud,
    Tractor,
    Leaf,
    Building,
    TreePine,
    Droplets,
    Sprout,
} from 'lucide-react';

interface CoreLayerTogglesProps {
    compact?: boolean;
}

interface LayerDefinition {
    id: LayerType;
    labelKey: string;
    icon: React.ReactNode;
    color: string;
}

const LAYER_DEFINITIONS: LayerDefinition[] = [
    { id: 'parcels', labelKey: 'viewer.layers.parcels', icon: <MapPin className="w-4 h-4" />, color: 'text-green-600' },
    { id: 'robots', labelKey: 'viewer.layers.robots', icon: <Bot className="w-4 h-4" />, color: 'text-blue-600' },
    { id: 'sensors', labelKey: 'viewer.layers.sensors', icon: <Gauge className="w-4 h-4" />, color: 'text-orange-600' },
    { id: 'machines', labelKey: 'viewer.layers.machines', icon: <Tractor className="w-4 h-4" />, color: 'text-amber-600' },
    { id: 'weather', labelKey: 'viewer.layers.weather', icon: <Cloud className="w-4 h-4" />, color: 'text-sky-600' },
    { id: 'livestock', labelKey: 'viewer.layers.livestock', icon: <Leaf className="w-4 h-4" />, color: 'text-emerald-600' },
    { id: 'buildings', labelKey: 'viewer.layers.buildings', icon: <Building className="w-4 h-4" />, color: 'text-slate-600' },
    { id: 'trees', labelKey: 'viewer.layers.trees', icon: <TreePine className="w-4 h-4" />, color: 'text-lime-600' },
    { id: 'waterSources', labelKey: 'viewer.layers.water', icon: <Droplets className="w-4 h-4" />, color: 'text-cyan-600' },
    { id: 'vegetation', labelKey: 'viewer.layers.vegetation', icon: <Sprout className="w-4 h-4" />, color: 'text-green-700' },
];

const CoreLayerToggles: React.FC<CoreLayerTogglesProps> = ({ compact = false }) => {
    const { toggleLayer, isLayerActive } = useViewer();
    const { t } = useI18n();

    if (compact) {
        return (
            <div className="flex flex-wrap gap-1">
                {LAYER_DEFINITIONS.map(layer => (
                    <button
                        key={layer.id}
                        onClick={() => toggleLayer(layer.id)}
                        className={`p-2 rounded-lg transition-all ${isLayerActive(layer.id)
                            ? 'bg-blue-50 text-blue-600 border border-blue-200'
                            : 'hover:bg-slate-50 text-slate-400 border border-transparent'
                            }`}
                        title={t(layer.labelKey)}
                    >
                        <span className={isLayerActive(layer.id) ? layer.color : 'text-slate-400'}>
                            {layer.icon}
                        </span>
                    </button>
                ))}
            </div>
        );
    }

    return (
        <div className="space-y-1">
            {LAYER_DEFINITIONS.map(layer => (
                <button
                    key={layer.id}
                    onClick={() => toggleLayer(layer.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all ${isLayerActive(layer.id)
                        ? 'bg-blue-50 text-blue-700 border border-blue-200'
                        : 'hover:bg-slate-50 text-slate-600'
                        }`}
                >
                    <span className={layer.color}>{layer.icon}</span>
                    <span className="flex-1 text-left text-sm">{t(layer.labelKey)}</span>
                    <div className={`w-3 h-3 rounded-full transition-colors ${isLayerActive(layer.id) ? 'bg-blue-500' : 'bg-slate-300'
                        }`} />
                </button>
            ))}
        </div>
    );
};

export default CoreLayerToggles;

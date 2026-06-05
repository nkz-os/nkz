// =============================================================================
// Core Layer Toggles - Built-in widget for the layer manager
// =============================================================================
// Layer toggle controls for the unified viewer.
// This is a "core" widget that's always available, not loaded from a module.

import React from 'react';
import { useViewer, LayerType } from '@/context/ViewerContext';
import { useI18n } from '@/context/I18nContext';
import { Button } from '@nekazari/ui-kit';
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
    Camera,
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
    { id: 'parcels', labelKey: 'viewer.layers.parcels', icon: <MapPin className="w-4 h-4" />, color: 'text-nkz-success' },
    { id: 'robots', labelKey: 'viewer.layers.robots', icon: <Bot className="w-4 h-4" />, color: 'text-nkz-info' },
    { id: 'sensors', labelKey: 'viewer.layers.sensors', icon: <Gauge className="w-4 h-4" />, color: 'text-orange-600' },
    { id: 'machines', labelKey: 'viewer.layers.machines', icon: <Tractor className="w-4 h-4" />, color: 'text-amber-600' },
    { id: 'weather', labelKey: 'viewer.layers.weather', icon: <Cloud className="w-4 h-4" />, color: 'text-sky-600' },
    { id: 'livestock', labelKey: 'viewer.layers.livestock', icon: <Leaf className="w-4 h-4" />, color: 'text-emerald-600' },
    { id: 'buildings', labelKey: 'viewer.layers.buildings', icon: <Building className="w-4 h-4" />, color: 'text-slate-600' },
    { id: 'trees', labelKey: 'viewer.layers.trees', icon: <TreePine className="w-4 h-4" />, color: 'text-lime-600' },
    { id: 'waterSources', labelKey: 'viewer.layers.water', icon: <Droplets className="w-4 h-4" />, color: 'text-cyan-600' },
    { id: 'vegetation', labelKey: 'viewer.layers.vegetation', icon: <Sprout className="w-4 h-4" />, color: 'text-nkz-success' },
    { id: 'fieldPhotos', labelKey: 'viewer.layers.fieldPhotos', icon: <Camera className="w-4 h-4" />, color: 'text-rose-600' },
];

const CoreLayerToggles: React.FC<CoreLayerTogglesProps> = ({ compact = false }) => {
    const { toggleLayer, isLayerActive } = useViewer();
    const { t } = useI18n();

    if (compact) {
        return (
            <div className="flex flex-wrap gap-1">
                {LAYER_DEFINITIONS.map(layer => (
                    <Button
                        key={layer.id}
                        onClick={() => toggleLayer(layer.id)}
                        className={`p-2 rounded-lg transition-all ${isLayerActive(layer.id)
                            ? 'bg-nkz-info-light text-nkz-info border border-blue-200'
                            : 'hover:bg-slate-50 text-slate-400 border border-transparent'
                            }`}
                        title={t(layer.labelKey)}
                    >
                        <span className={isLayerActive(layer.id) ? layer.color : 'text-slate-400'}>
                            {layer.icon}
                        </span>
                    </Button>
                ))}
            </div>
        );
    }

    return (
        <div className="space-y-1">
            {LAYER_DEFINITIONS.map(layer => (
                <Button
                    key={layer.id}
                    onClick={() => toggleLayer(layer.id)}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all ${isLayerActive(layer.id)
                        ? 'bg-nkz-info-light text-nkz-info border border-blue-200'
                        : 'hover:bg-slate-50 text-slate-600'
                        }`}
                >
                    <span className={layer.color}>{layer.icon}</span>
                    <span className="flex-1 text-left text-sm">{t(layer.labelKey)}</span>
                    <div className={`w-3 h-3 rounded-full transition-colors ${isLayerActive(layer.id) ? 'bg-nkz-info-light0' : 'bg-slate-300'
                        }`} />
                </Button>
            ))}
        </div>
    );
};

export default CoreLayerToggles;

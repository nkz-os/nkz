/**
 * DefaultIconSelector - Select from predefined icons or upload custom
 * 
 * Provides a gallery of default icons based on entity type, allowing users
 * to quickly select an appropriate icon without uploading.
 */

import React, { useState } from 'react';
import { Button } from '@nekazari/ui-kit';
import { 
  MapPin, Gauge, Bot, Building2, Droplets, Trees, Zap, Tractor,
  Leaf, Activity, Sun, Thermometer, Wind, CloudRain, Sprout,
  Factory, Warehouse, Fence, CircleDot, Radio, Wifi, Camera,
  Check, ChevronDown, ChevronUp
} from 'lucide-react';

// Available default icons with their SVG data URIs
const DEFAULT_ICONS: Record<string, { icon: React.ComponentType<any>; label: string; category: string }> = {
  // Agriculture
  'leaf': { icon: Leaf, label: 'Planta', category: 'Agricultura' },
  'sprout': { icon: Sprout, label: 'Brote', category: 'Agricultura' },
  'trees': { icon: Trees, label: 'Árboles', category: 'Agricultura' },
  'mappin': { icon: MapPin, label: 'Parcela', category: 'Agricultura' },
  
  // Infrastructure
  'building': { icon: Building2, label: 'Edificio', category: 'Infraestructura' },
  'warehouse': { icon: Warehouse, label: 'Almacén', category: 'Infraestructura' },
  'factory': { icon: Factory, label: 'Fábrica', category: 'Infraestructura' },
  'fence': { icon: Fence, label: 'Cercado', category: 'Infraestructura' },
  
  // Water
  'droplets': { icon: Droplets, label: 'Agua', category: 'Agua' },
  'cloudrain': { icon: CloudRain, label: 'Lluvia', category: 'Agua' },
  
  // Sensors
  'gauge': { icon: Gauge, label: 'Sensor', category: 'Sensores' },
  'thermometer': { icon: Thermometer, label: 'Temperatura', category: 'Sensores' },
  'activity': { icon: Activity, label: 'Actividad', category: 'Sensores' },
  'radio': { icon: Radio, label: 'Radio', category: 'Sensores' },
  'wifi': { icon: Wifi, label: 'WiFi', category: 'Sensores' },
  'camera': { icon: Camera, label: 'Cámara', category: 'Sensores' },
  
  // Weather
  'sun': { icon: Sun, label: 'Sol', category: 'Meteorología' },
  'wind': { icon: Wind, label: 'Viento', category: 'Meteorología' },
  
  // Fleet
  'bot': { icon: Bot, label: 'Robot', category: 'Flota' },
  'tractor': { icon: Tractor, label: 'Tractor', category: 'Flota' },
  
  // Energy
  'zap': { icon: Zap, label: 'Energía', category: 'Energía' },
  
  // Generic
  'circledot': { icon: CircleDot, label: 'Punto', category: 'General' },
};

// Map entity types to suggested icons
const ENTITY_ICON_SUGGESTIONS: Record<string, string[]> = {
  AgriParcel: ['mappin', 'leaf', 'sprout'],
  Vineyard: ['leaf', 'sprout', 'mappin'],
  OliveGrove: ['trees', 'leaf', 'mappin'],
  AgriTree: ['trees', 'leaf', 'sprout'],
  AgriBuilding: ['building', 'warehouse', 'factory'],
  WaterSource: ['droplets', 'cloudrain'],
  Well: ['droplets', 'circledot'],
  IrrigationOutlet: ['droplets', 'circledot'],
  AgriSensor: ['gauge', 'thermometer', 'activity'],
  Device: ['wifi', 'radio', 'gauge'],
  WeatherObserved: ['sun', 'wind', 'thermometer'],
  AutonomousMobileRobot: ['bot', 'activity'],
  ManufacturingMachine: ['tractor', 'activity'],
  PhotovoltaicInstallation: ['sun', 'zap'],
  EnergyStorageSystem: ['zap', 'activity'],
};

interface DefaultIconSelectorProps {
  entityType?: string;
  selectedIcon?: string | null;
  onSelect: (iconKey: string | null) => void;
}

export const DefaultIconSelector: React.FC<DefaultIconSelectorProps> = ({
  entityType,
  selectedIcon,
  onSelect,
}) => {
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Get suggested icons for this entity type
  const suggestedKeys = entityType ? ENTITY_ICON_SUGGESTIONS[entityType] || [] : [];
  const suggestedIcons = suggestedKeys.map(key => ({ key, ...DEFAULT_ICONS[key] })).filter(i => i.icon);
  
  // Group all icons by category for expanded view
  const groupedIcons = Object.entries(DEFAULT_ICONS).reduce((acc, [key, data]) => {
    if (!acc[data.category]) acc[data.category] = [];
    acc[data.category].push({ key, ...data });
    return acc;
  }, {} as Record<string, Array<{ key: string; icon: React.ComponentType<any>; label: string; category: string }>>);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="block text-sm font-medium text-gray-700">
          Icono por defecto
        </label>
        <Button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-xs text-nkz-info hover:text-blue-800 flex items-center gap-1"
        >
          {isExpanded ? (
            <>Menos opciones <ChevronUp className="w-3 h-3" /></>
          ) : (
            <>Más opciones <ChevronDown className="w-3 h-3" /></>
          )}
        </Button>
      </div>

      {/* Suggested Icons */}
      {suggestedIcons.length > 0 && (
        <div>
          <p className="text-xs text-nkz-muted mb-2">Sugeridos para {entityType}</p>
          <div className="flex flex-wrap gap-2">
            {suggestedIcons.map(({ key, icon: Icon, label }) => (
              <Button
                key={key}
                type="button"
                onClick={() => onSelect(selectedIcon === key ? null : key)}
                className={`p-3 rounded-lg border-2 transition flex flex-col items-center gap-1 min-w-[70px] ${
                  selectedIcon === key
                    ? 'border-green-500 bg-nkz-success-light'
                    : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary'
                }`}
              >
                <Icon className={`w-6 h-6 ${selectedIcon === key ? 'text-nkz-success' : 'text-gray-600'}`} />
                <span className="text-xs text-gray-600">{label}</span>
                {selectedIcon === key && (
                  <Check className="w-3 h-3 text-nkz-success absolute top-1 right-1" />
                )}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* Expanded All Icons */}
      {isExpanded && (
        <div className="border border-nkz-border rounded-lg p-3 max-h-[250px] overflow-y-auto">
          {Object.entries(groupedIcons).map(([category, icons]) => (
            <div key={category} className="mb-3 last:mb-0">
              <p className="text-xs font-medium text-nkz-muted mb-2">{category}</p>
              <div className="flex flex-wrap gap-2">
                {icons.map(({ key, icon: Icon, label }) => (
                  <Button
                    key={key}
                    type="button"
                    onClick={() => onSelect(selectedIcon === key ? null : key)}
                    className={`p-2 rounded-lg border transition flex items-center gap-2 ${
                      selectedIcon === key
                        ? 'border-green-500 bg-nkz-success-light'
                        : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary'
                    }`}
                  >
                    <Icon className={`w-4 h-4 ${selectedIcon === key ? 'text-nkz-success' : 'text-nkz-muted'}`} />
                    <span className="text-xs text-gray-600">{label}</span>
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Selected indicator */}
      {selectedIcon && (
        <div className="flex items-center gap-2 text-sm text-nkz-success bg-nkz-success-light px-3 py-2 rounded-lg">
          {(() => {
            const iconData = DEFAULT_ICONS[selectedIcon];
            if (iconData) {
              const Icon = iconData.icon;
              return (
                <>
                  <Icon className="w-4 h-4" />
                  <span>Icono seleccionado: {iconData.label}</span>
                </>
              );
            }
            return null;
          })()}
        </div>
      )}

      <p className="text-xs text-nkz-muted">
        Selecciona un icono por defecto o sube uno personalizado abajo.
      </p>
    </div>
  );
};

// Export icon lookup for use in other components
export const getDefaultIconComponent = (iconKey: string): React.ComponentType<any> | null => {
  return DEFAULT_ICONS[iconKey]?.icon || null;
};

export const DEFAULT_ICON_KEYS = DEFAULT_ICONS;


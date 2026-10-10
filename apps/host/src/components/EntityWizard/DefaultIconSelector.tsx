/**
 * DefaultIconSelector - Select from predefined icons or upload custom
 * 
 * Provides a gallery of default icons based on entity type, allowing users
 * to quickly select an appropriate icon without uploading.
 */

import React, { useState } from 'react';
import { Button } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { 
  MapPin, Gauge, Bot, Building2, Droplets, Trees, Zap, Tractor,
  Leaf, Activity, Sun, Thermometer, Wind, CloudRain, Sprout,
  Factory, Warehouse, Fence, CircleDot, Radio, Wifi, Camera,
  Check, ChevronDown, ChevronUp
} from 'lucide-react';

// Available default icons with their SVG data URIs
const DEFAULT_ICONS: Record<string, { icon: React.ComponentType<any>; labelKey: string; categoryKey: string }> = {
  // Agriculture
  'leaf': { icon: Leaf, labelKey: 'label_leaf', categoryKey: 'category_agriculture' },
  'sprout': { icon: Sprout, labelKey: 'label_sprout', categoryKey: 'category_agriculture' },
  'trees': { icon: Trees, labelKey: 'label_trees', categoryKey: 'category_agriculture' },
  'mappin': { icon: MapPin, labelKey: 'label_parcel', categoryKey: 'category_agriculture' },
  
  // Infrastructure
  'building': { icon: Building2, labelKey: 'label_building', categoryKey: 'category_infrastructure' },
  'warehouse': { icon: Warehouse, labelKey: 'label_warehouse', categoryKey: 'category_infrastructure' },
  'factory': { icon: Factory, labelKey: 'label_factory', categoryKey: 'category_infrastructure' },
  'fence': { icon: Fence, labelKey: 'label_fence', categoryKey: 'category_infrastructure' },
  
  // Water
  'droplets': { icon: Droplets, labelKey: 'label_water', categoryKey: 'category_water' },
  'cloudrain': { icon: CloudRain, labelKey: 'label_rain', categoryKey: 'category_water' },
  
  // Sensors
  'gauge': { icon: Gauge, labelKey: 'label_sensor', categoryKey: 'category_sensors' },
  'thermometer': { icon: Thermometer, labelKey: 'label_temperature', categoryKey: 'category_sensors' },
  'activity': { icon: Activity, labelKey: 'label_activity', categoryKey: 'category_sensors' },
  'radio': { icon: Radio, labelKey: 'label_radio', categoryKey: 'category_sensors' },
  'wifi': { icon: Wifi, labelKey: 'label_wifi', categoryKey: 'category_sensors' },
  'camera': { icon: Camera, labelKey: 'label_camera', categoryKey: 'category_sensors' },
  
  // Weather
  'sun': { icon: Sun, labelKey: 'label_sun', categoryKey: 'category_weather' },
  'wind': { icon: Wind, labelKey: 'label_wind', categoryKey: 'category_weather' },
  
  // Fleet
  'bot': { icon: Bot, labelKey: 'label_robot', categoryKey: 'category_fleet' },
  'tractor': { icon: Tractor, labelKey: 'label_tractor', categoryKey: 'category_fleet' },
  
  // Energy
  'zap': { icon: Zap, labelKey: 'label_energy', categoryKey: 'category_energy' },
  
  // Generic
  'circledot': { icon: CircleDot, labelKey: 'label_dot', categoryKey: 'category_general' },
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
  const { t } = useI18n();
  const [isExpanded, setIsExpanded] = useState(false);
  
  // Get suggested icons for this entity type
  const suggestedKeys = entityType ? ENTITY_ICON_SUGGESTIONS[entityType] || [] : [];
  const suggestedIcons = suggestedKeys.map(key => ({ key, ...DEFAULT_ICONS[key] })).filter(i => i.icon);
  
  // Group all icons by category for expanded view
  const groupedIcons = Object.entries(DEFAULT_ICONS).reduce((acc, [key, data]) => {
    if (!acc[data.categoryKey]) acc[data.categoryKey] = [];
    acc[data.categoryKey].push({ key, ...data });
    return acc;
  }, {} as Record<string, Array<{ key: string; icon: React.ComponentType<any>; labelKey: string; categoryKey: string }>>);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="block text-sm font-medium text-nkz-text-primary">
          {t('wizard.icons.default_icon')}
        </label>
        <Button variant="ghost"
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-xs text-nkz-info hover:text-nkz-info-strong flex items-center gap-1"
        >
          {isExpanded ? (
            <>{t('wizard.icons.fewer_options')} <ChevronUp className="w-3 h-3" /></>
          ) : (
            <>{t('wizard.icons.more_options')} <ChevronDown className="w-3 h-3" /></>
          )}
        </Button>
      </div>

      {/* Suggested Icons */}
      {suggestedIcons.length > 0 && (
        <div>
          <p className="text-xs text-nkz-muted mb-2">{t('wizard.icons.suggested_for', { type: entityType })}</p>
          <div className="flex flex-wrap gap-2">
            {suggestedIcons.map(({ key, icon: Icon, labelKey }) => (
              <Button variant="ghost"
                key={key}
                type="button"
                onClick={() => onSelect(selectedIcon === key ? null : key)}
                className={`p-3 rounded-lg border-2 transition flex flex-col items-center gap-1 min-w-[70px] ${
                  selectedIcon === key
                    ? 'border-green-500 bg-nkz-surface-sunken'
                    : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary'
                }`}
              >
                <Icon className={`w-6 h-6 ${selectedIcon === key ? 'text-nkz-success' : 'text-nkz-text-secondary'}`} />
                <span className="text-xs text-nkz-text-secondary">{t(`wizard.icons.${labelKey}`)}</span>
                {selectedIcon === key && (
                  <Check className="w-3 h-3 text-nkz-success-strong absolute top-1 right-1" />
                )}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* Expanded All Icons */}
      {isExpanded && (
        <div className="border border-nkz-border rounded-lg p-3 max-h-[250px] overflow-y-auto">
          {Object.entries(groupedIcons).map(([categoryKey, icons]) => (
            <div key={categoryKey} className="mb-3 last:mb-0">
              <p className="text-xs font-medium text-nkz-muted mb-2">{t(`wizard.icons.${categoryKey}`)}</p>
              <div className="flex flex-wrap gap-2">
                {icons.map(({ key, icon: Icon, labelKey }) => (
                  <Button variant="ghost"
                    key={key}
                    type="button"
                    onClick={() => onSelect(selectedIcon === key ? null : key)}
                    className={`p-2 rounded-lg border transition flex items-center gap-2 ${
                      selectedIcon === key
                        ? 'border-green-500 bg-nkz-surface-sunken'
                        : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary'
                    }`}
                  >
                    <Icon className={`w-4 h-4 ${selectedIcon === key ? 'text-nkz-success' : 'text-nkz-muted'}`} />
                    <span className="text-xs text-nkz-text-secondary">{t(`wizard.icons.${labelKey}`)}</span>
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Selected indicator */}
      {selectedIcon && (
        <div className="flex items-center gap-2 text-sm text-nkz-success-strong bg-nkz-success-soft px-3 py-2 rounded-lg">
          {(() => {
            const iconData = DEFAULT_ICONS[selectedIcon];
            if (iconData) {
              const Icon = iconData.icon;
              return (
                <>
                  <Icon className="w-4 h-4" />
                  <span>{t('wizard.icons.selected', { label: t(`wizard.icons.${iconData.labelKey}`) })}</span>
                </>
              );
            }
            return null;
          })()}
        </div>
      )}

      <p className="text-xs text-nkz-muted">
        {t('wizard.icons.hint')}
      </p>
    </div>
  );
};

// Export icon lookup for use in other components
export const getDefaultIconComponent = (iconKey: string): React.ComponentType<any> | null => {
  return DEFAULT_ICONS[iconKey]?.icon || null;
};

export const DEFAULT_ICON_KEYS = DEFAULT_ICONS;


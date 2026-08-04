// =============================================================================
// Asset Row Component - Table Row for Asset Manager
// =============================================================================

import React, { memo } from 'react';
import {
/* eslint-disable @typescript-eslint/no-explicit-any */
  CheckSquare,
  Square,
  MapPin,
  MoreVertical,
  Bot,
  Gauge,
  Tractor,
  TreeDeciduous,
  Building2,
  Droplets,
  CloudSun,
  Beef,
  Map,
  Leaf,
  Cpu,
  CircleDot,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { es } from 'date-fns/locale';
import { UnifiedAsset, ASSET_TYPE_REGISTRY } from '@/types/assets';
import { useI18n } from '@/context/I18nContext';
import { Button } from '@nekazari/ui-kit';

// =============================================================================
// Icon Map
// =============================================================================

const ICON_MAP: Record<string, React.ComponentType<{ className?: string }>> = {
  'bot': Bot,
  'gauge': Gauge,
  'tractor': Tractor,
  'tree-deciduous': TreeDeciduous,
  'building-2': Building2,
  'droplets': Droplets,
  'droplet': Droplets,
  'cloud-sun': CloudSun,
  'beef': Beef,
  'map': Map,
  'map-pin': MapPin,
  'leaf': Leaf,
  'cpu': Cpu,
  'circle-dot': CircleDot,
  'trees': TreeDeciduous,
  'grape': Leaf,
  'apple': Leaf,
  'waves': Droplets,
  'wrench': Gauge,
  'users': Beef,
  'truck': Tractor,
  'building': Building2,
};

// =============================================================================
// Props
// =============================================================================

export interface AssetRowProps {
  asset: UnifiedAsset;
  isSelected: boolean;
  onSelect: () => void;
  onClick: () => void;
  onDoubleClick?: () => void;
  onContextMenu?: (e: React.MouseEvent) => void;
  compact?: boolean;
}

// =============================================================================
// Component
// =============================================================================

export const AssetRow: React.FC<AssetRowProps> = memo(({
  asset,
  isSelected,
  onSelect,
  onClick,
  onDoubleClick,
  onContextMenu,
  compact = false,
}) => {
  const { t } = useI18n();
  const typeInfo = ASSET_TYPE_REGISTRY[asset.type];

  // Get icon component
  const iconName = typeInfo?.icon || asset.icon || 'map-pin';
  const IconComponent = ICON_MAP[iconName] || MapPin;
  
  // Status colors
  const statusColors: Record<string, { bg: string; text: string; dot: string }> = {
    active: { bg: 'bg-nkz-success-light', text: 'text-nkz-success-strong', dot: 'bg-nkz-success-light0' },
    inactive: { bg: 'bg-slate-100', text: 'text-slate-600', dot: 'bg-slate-400' },
    maintenance: { bg: 'bg-amber-100', text: 'text-amber-700', dot: 'bg-amber-500' },
    error: { bg: 'bg-nkz-error-light', text: 'text-nkz-error', dot: 'bg-nkz-error-light0' },
    offline: { bg: 'bg-slate-100', text: 'text-slate-500', dot: 'bg-slate-400' },
    unknown: { bg: 'bg-slate-50', text: 'text-slate-500', dot: 'bg-slate-300' },
  };
  
  const statusStyle = statusColors[asset.status] || statusColors.unknown;
  
  // Category colors for icon background
  const categoryColors: Record<string, string> = {
    parcels: 'bg-nkz-success-light text-nkz-success',
    sensors: 'bg-teal-100 text-teal-600',
    fleet: 'bg-indigo-100 text-indigo-600',
    infrastructure: 'bg-slate-100 text-slate-600',
    vegetation: 'bg-emerald-100 text-emerald-600',
    livestock: 'bg-amber-100 text-amber-600',
    water: 'bg-nkz-info-light text-nkz-info',
    weather: 'bg-sky-100 text-sky-600',
  };
  
  const iconStyle = categoryColors[asset.category] || 'bg-slate-100 text-slate-600';
  
  return (
    <div
      className={`flex items-center gap-2 px-4 py-2.5 cursor-pointer transition-colors ${
        isSelected
          ? 'bg-white/15 hover:bg-white/20'
          : 'hover:bg-white/10'
      }`}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
    >
      {/* Checkbox */}
      <Button
        onClick={((e: any) => {
          e.stopPropagation();
          onSelect();
        }) as any}
        className="flex-shrink-0 p-1 rounded hover:bg-slate-200"
      >
        {isSelected
          ? <CheckSquare className="w-4 h-4 text-blue-400" />
          : <Square className="w-4 h-4 text-white/30 hover:text-white/50" />
        }
      </Button>
      
      {/* Icon + Name */}
      <div className="flex-1 min-w-[180px] flex items-center gap-3">
        <div className={`flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center ${iconStyle}`}>
          <IconComponent className="w-4 h-4" />
        </div>
        
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm text-white truncate" title={asset.name}>
              {asset.name}
            </span>
            {asset.hasLocation && (
              <MapPin className="w-3 h-3 text-slate-400 flex-shrink-0" />
            )}
          </div>
          {!compact && asset.description && (
            <p className="text-xs text-white/50 truncate" title={asset.description}>
              {asset.description}
            </p>
          )}
        </div>
      </div>
      
      {/* Type */}
      <div className="w-28 flex-shrink-0">
        <span className="text-xs text-white/60 bg-white/10 px-2 py-0.5 rounded-full truncate inline-block max-w-full" title={typeInfo?.label || asset.type}>
          {typeInfo?.label || asset.type}
        </span>
      </div>
      
      {/* Status */}
      <div className="w-24 flex-shrink-0">
        <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full ${statusStyle.bg} ${statusStyle.text}`} title={asset.statusLabel || asset.status}>
          <span className={`w-1.5 h-1.5 rounded-full ${statusStyle.dot}`} />
          <span className="capitalize truncate">
            {asset.status === 'active' ? t('entities.status.active') :
             asset.status === 'inactive' ? t('entities.status.inactive') :
             asset.status === 'maintenance' ? t('entities.status.maintenance') :
             asset.status === 'error' ? t('entities.status.error') :
             asset.status === 'offline' ? t('entities.status.offline') :
             t('entities.status.unknown')}
          </span>
        </span>
      </div>
      
      {/* Location (only on medium+ screens and non-compact) */}
      {!compact && (
        <div className="w-32 flex-shrink-0 hidden md:block">
          <span className="text-xs text-white/50 truncate block" title={asset.municipality || (asset.hasLocation ? t('entities.filters.with_location') : '—')}>
            {asset.municipality || (asset.hasLocation ? t('entities.filters.with_location') : '—')}
          </span>
        </div>
      )}
      
      {/* Last Seen / Telemetry */}
      {!compact && asset.lastSeen && (
        <div className="w-28 flex-shrink-0 hidden xl:block text-right">
          <span className="text-xs text-white/40" title={formatDistanceToNow(asset.lastSeen, { addSuffix: true, locale: es })}>
            {formatDistanceToNow(asset.lastSeen, { addSuffix: true, locale: es })}
          </span>
        </div>
      )}
      
      {/* Actions */}
      <Button
        onClick={((e: any) => {
          e.stopPropagation();
          onContextMenu?.(e);
        }) as any}
        className="flex-shrink-0 p-1 rounded hover:bg-white/10 text-white/40 hover:text-white/70"
      >
        <MoreVertical className="w-4 h-4" />
      </Button>
    </div>
  );
});

AssetRow.displayName = 'AssetRow';

export default AssetRow;


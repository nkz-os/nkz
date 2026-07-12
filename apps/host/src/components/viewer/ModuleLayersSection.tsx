// =============================================================================
// Module Layers Section - Unified viewer-layer toggle/opacity/status panel
// =============================================================================
// Renders every layer any module has declared via `defineModule({ viewerLayers })`
// (see @nekazari/sdk's LayerRegistry). This is the SINGLE place a module's
// viewer layer can be toggled — HARD CUT (plan 2026-07-12 §B2): there is no
// module-local toggle mechanism anymore. Reacts to late registrations (remote
// modules whose federated entry evaluates after this panel first renders) via
// `useSyncExternalStore(LayerRegistry.subscribe, ...)` — no polling.

import React, { useMemo, useSyncExternalStore } from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import { LayerRegistry, type ViewerLayerEntry, type ViewerLayerStatus } from '@nekazari/sdk';
import { Switch, Slider, Tooltip, Spinner } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

/** Statuses that warrant a small hint icon + tooltip (no data / failed / needs a selection). */
const HINT_STATUSES = new Set<ViewerLayerStatus>(['empty', 'error', 'noSelection']);

function groupKeyFor(entry: ViewerLayerEntry): string {
  return entry.group || entry.moduleId;
}

const LayerStatusIndicator: React.FC<{ status: ViewerLayerStatus }> = ({ status }) => {
  const { t } = useI18n();

  if (status === 'loading') {
    return (
      <Tooltip content={t('viewer.moduleLayers.status.loading')}>
        <span className="inline-flex items-center" data-testid="layer-status-loading">
          <Spinner size="sm" />
        </span>
      </Tooltip>
    );
  }

  if (!HINT_STATUSES.has(status)) {
    return null;
  }

  const Icon = status === 'error' ? AlertTriangle : Info;
  const colorClass = status === 'error' ? 'text-nkz-error' : 'text-slate-400 dark:text-slate-500';

  return (
    <Tooltip content={t(`viewer.moduleLayers.status.${status}`)}>
      <span className={`inline-flex items-center ${colorClass}`} data-testid={`layer-status-${status}`}>
        <Icon className="w-3.5 h-3.5" />
      </span>
    </Tooltip>
  );
};

const ModuleLayerRow: React.FC<{ entry: ViewerLayerEntry }> = ({ entry }) => {
  const { t } = useI18n();

  return (
    <div
      className="px-3 py-2 rounded-lg border border-transparent hover:bg-slate-50 dark:hover:bg-slate-800"
      data-testid={`module-layer-row-${entry.id}`}
    >
      <div className="flex items-center gap-2">
        <span className="flex-1 text-left text-sm text-slate-600 dark:text-slate-300 truncate">
          {t(entry.titleKey)}
        </span>
        <LayerStatusIndicator status={entry.status} />
        <Switch
          checked={entry.visible}
          onChange={(visible) => LayerRegistry.setVisible(entry.id, visible)}
        />
      </div>
      {entry.supportsOpacity && (
        <div className="pt-1" data-testid={`module-layer-opacity-${entry.id}`}>
          <Slider
            value={entry.opacity}
            onChange={(opacity) => LayerRegistry.setOpacity(entry.id, opacity)}
            min={0}
            max={100}
            step={1}
            label={t('viewer.moduleLayers.opacityLabel')}
            unit="%"
          />
        </div>
      )}
    </div>
  );
};

export const ModuleLayersSection: React.FC = () => {
  const { t } = useI18n();

  const layers = useSyncExternalStore(
    LayerRegistry.subscribe,
    LayerRegistry.getAllLayers,
    LayerRegistry.getAllLayers,
  );

  const groups = useMemo(() => {
    const byGroup = new Map<string, ViewerLayerEntry[]>();
    for (const entry of layers) {
      const key = groupKeyFor(entry);
      const existing = byGroup.get(key);
      if (existing) {
        existing.push(entry);
      } else {
        byGroup.set(key, [entry]);
      }
    }
    return Array.from(byGroup.entries());
  }, [layers]);

  if (layers.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-slate-400 dark:text-slate-500">
        {t('viewer.moduleLayers.empty')}
      </p>
    );
  }

  return (
    <div className="space-y-2" data-testid="module-layers-section">
      {groups.map(([group, entries]) => (
        <div key={group}>
          <div className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            {group}
          </div>
          <div className="space-y-1">
            {entries.map((entry) => (
              <ModuleLayerRow key={entry.id} entry={entry} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

export default ModuleLayersSection;

// =============================================================================
// Viewer Modules Section — per-module switches at the top of the right panel
// =============================================================================
// Each module that contributes widgets to the viewer gets a switch that shows
// or hides the whole module (map layers and panels alike). Modules with a
// layer-toggle widget can be expanded to show their own layer options.
// Per-parcel backend activation is a different concept and lives in the
// parcel details ("parcel services").

import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Puzzle } from 'lucide-react';
import { Button, Switch } from '@nekazari/ui-kit';
import { LayerMenuRowEmbeddedContext } from '@nekazari/module-kit';
import { useModules, type ModuleDefinition } from '@/context/ModuleContext';
import { useSlotRegistry } from '@/context/SlotRegistry';
import { useI18n } from '@/context/I18nContext';
import { SlotRenderer } from '@/components/SlotRenderer';

const OPEN_STORAGE_KEY = 'nkz.viewer.modulesSection.open';

const hasViewerWidgets = (m: ModuleDefinition): boolean =>
    !!m.viewerSlots &&
    Object.entries(m.viewerSlots).some(([key, list]) => key !== 'moduleProvider' && Array.isArray(list) && list.length > 0);

const hasLayerOptions = (m: ModuleDefinition): boolean =>
    (m.viewerSlots?.['layer-toggle']?.length ?? 0) > 0;

const ModuleIcon: React.FC<{ module: ModuleDefinition }> = ({ module }) => {
    const emoji = module.metadata?.icon;
    if (typeof emoji === 'string' && emoji.length > 0 && emoji.length <= 2) {
        return <span className="w-4 text-center" aria-hidden="true">{emoji}</span>;
    }
    return <Puzzle className="w-4 h-4 text-slate-500" aria-hidden="true" />;
};

const ModuleRow: React.FC<{ module: ModuleDefinition }> = ({ module }) => {
    const { t } = useI18n();
    const { isModuleActive, toggleModule } = useSlotRegistry();
    const [expanded, setExpanded] = useState(false);
    const active = isModuleActive(module.id);
    const name = module.displayName || module.name || module.id;
    const canExpand = active && hasLayerOptions(module);

    return (
        <div className="rounded-lg hover:bg-slate-50 dark:hover:bg-slate-800/60">
            <div className="flex items-center gap-2 px-2 py-1.5">
                <ModuleIcon module={module} />
                <div className="flex-1 min-w-0 text-sm text-slate-700 dark:text-slate-200">
                    <Switch
                        checked={active}
                        onChange={() => toggleModule(module.id)}
                        label={name}
                        labelPosition="left"
                    />
                </div>
                {canExpand && (
                    <Button
                        type="button"
                        onClick={() => setExpanded(v => !v)}
                        aria-expanded={expanded}
                        aria-label={t('viewer.modulesSection.options')}
                        className="p-1 rounded text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                    >
                        {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </Button>
                )}
            </div>
            {canExpand && expanded && (
                <div className="px-2 pb-2">
                    <LayerMenuRowEmbeddedContext.Provider value={true}>
                        <SlotRenderer slot="layer-toggle" moduleId={module.id} inline />
                    </LayerMenuRowEmbeddedContext.Provider>
                </div>
            )}
        </div>
    );
};

const readOpen = (): boolean => {
    try {
        return localStorage.getItem(OPEN_STORAGE_KEY) !== 'false';
    } catch {
        return true;
    }
};

export const ViewerModulesSection: React.FC = () => {
    const { t } = useI18n();
    const { modules } = useModules();
    const [open, setOpen] = useState<boolean>(readOpen);

    useEffect(() => {
        try {
            localStorage.setItem(OPEN_STORAGE_KEY, String(open));
        } catch {
            /* private mode or quota */
        }
    }, [open]);

    const rows = modules.filter(m => m.id !== 'core' && hasViewerWidgets(m));
    if (rows.length === 0) return null;

    return (
        <section className="mb-3 rounded-lg border border-slate-200 dark:border-slate-700">
            <Button
                type="button"
                onClick={() => setOpen(v => !v)}
                aria-expanded={open}
                aria-label={t('viewer.modulesSection.title')}
                className="w-full px-3 py-2 flex items-center justify-between text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
            >
                <span className="text-xs font-semibold uppercase tracking-wider">{t('viewer.modulesSection.title')}</span>
                {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </Button>
            {open && (
                <div className="px-1 pb-2">
                    {rows.map(m => <ModuleRow key={m.id} module={m} />)}
                </div>
            )}
        </section>
    );
};

export default ViewerModulesSection;

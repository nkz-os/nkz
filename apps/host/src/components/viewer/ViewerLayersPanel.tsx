// =============================================================================
// Viewer Layers Panel — one-click access to core layers and module switches
// =============================================================================
// A floating "Layers" button on the map opens a docked panel. Each module that
// contributes widgets to the viewer gets a switch that shows or hides the whole
// module (map layers and panels alike); modules with a layer-toggle widget can
// be expanded to show their own layer options.

import React, { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Layers, Puzzle, X } from 'lucide-react';
import { Button, Switch } from '@nekazari/ui-kit';
import { LayerMenuRowEmbeddedContext } from '@nekazari/module-kit';
import { useViewer } from '@/context/ViewerContext';
import { useModules, type ModuleDefinition } from '@/context/ModuleContext';
import { useSlotRegistry } from '@/context/SlotRegistry';
import { useI18n } from '@/context/I18nContext';
import CoreLayerToggles from '@/components/viewer/CoreLayerToggles';
import { SlotRenderer } from '@/components/SlotRenderer';

const surface =
    'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-xl';

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
                        aria-label={t('viewer.layersModuleOptions')}
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

const isTypingTarget = (target: EventTarget | null): boolean => {
    const el = target as HTMLElement | null;
    if (!el) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
};

export const ViewerLayersPanel: React.FC = () => {
    const { t } = useI18n();
    const { isLayersPanelOpen, setLayersPanelOpen } = useViewer();
    const { modules } = useModules();

    useEffect(() => {
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape' && isLayersPanelOpen) {
                setLayersPanelOpen(false);
                return;
            }
            if (e.key === 'L' && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !isTypingTarget(e.target)) {
                e.preventDefault();
                setLayersPanelOpen(!isLayersPanelOpen);
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [isLayersPanelOpen, setLayersPanelOpen]);

    const moduleRows = modules.filter(m => m.id !== 'core' && hasViewerWidgets(m));

    return (
        <div className="absolute top-4 right-4 z-40 flex flex-col items-end gap-2">
            <Button
                type="button"
                onClick={() => setLayersPanelOpen(!isLayersPanelOpen)}
                aria-expanded={isLayersPanelOpen}
                aria-label={t(isLayersPanelOpen ? 'viewer.layersPanelClose' : 'viewer.layersPanelOpen')}
                title={t('viewer.layersPanelOpen')}
                className={`${surface} flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800`}
            >
                <Layers className="w-4 h-4" />
                <span>{t('viewer.layersTitle')}</span>
            </Button>

            {isLayersPanelOpen && (
                <div
                    role="dialog"
                    aria-label={t('viewer.layersTitle')}
                    className={`${surface} w-[320px] max-w-[calc(100vw-2rem)] max-h-[calc(100vh-6rem)] overflow-y-auto rounded-xl`}
                >
                    <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200 dark:border-slate-700">
                        <span className="flex items-center gap-2 text-sm font-medium text-slate-600 dark:text-slate-300">
                            <Layers className="w-4 h-4" />
                            {t('viewer.layersTitle')}
                        </span>
                        <Button
                            type="button"
                            onClick={() => setLayersPanelOpen(false)}
                            aria-label={t('viewer.layersPanelClose')}
                            className="p-1 rounded text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                        >
                            <X className="w-4 h-4" />
                        </Button>
                    </div>

                    <section className="px-2 py-2">
                        <h3 className="px-1 pb-1 text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                            {t('viewer.layersGroupCore')}
                        </h3>
                        <CoreLayerToggles />
                    </section>

                    <section className="px-2 py-2 border-t border-slate-200 dark:border-slate-700">
                        <h3 className="px-1 pb-1 text-xs font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                            {t('viewer.layersGroupModules')}
                        </h3>
                        {moduleRows.length === 0 ? (
                            <p className="px-1 py-2 text-sm text-slate-500">{t('viewer.layersNoModules')}</p>
                        ) : (
                            moduleRows.map(m => <ModuleRow key={m.id} module={m} />)
                        )}
                    </section>
                </div>
            )}
        </div>
    );
};

export default ViewerLayersPanel;

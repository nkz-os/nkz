// =============================================================================
// Slot Registry - Unified Viewer Widget Management
// =============================================================================
// Centralized system for managing which widgets are rendered in each slot
// of the Unified Command Center. Supports both local and remote modules.

import React, { createContext, useContext, useState, useCallback, useMemo, ReactNode, useEffect } from 'react';
import type { SlotType, SlotWidgetDefinition, ModuleViewerSlots } from '@nekazari/sdk';
import { useModules } from './ModuleContext';
import { useViewer } from './ViewerContext';
import { useAuth } from './KeycloakAuthContext';
import { getLocalModuleSlots, getAllLocalModuleIds } from '@/modules/registry';
import { resolveActiveModules, loadChoices, saveChoices, type ActivationChoices } from './moduleActivation';


// =============================================================================
// Core Widgets - Built-in widgets that are always available
// =============================================================================

// Lazy imports for core widgets
/* eslint-disable @typescript-eslint/no-explicit-any */
const CoreEntityTree = React.lazy(() => import('@/components/viewer/CoreEntityTree'));
const CoreContextPanel = React.lazy(() => import('@/components/viewer/CoreContextPanel'));
const CoreLayerToggles = React.lazy(() => import('@/components/viewer/CoreLayerToggles'));

/** Core module definition with built-in widgets */
const CORE_MODULE_SLOTS: ModuleViewerSlots = {
    'entity-tree': [{
        id: 'core-entity-tree',
        component: 'CoreEntityTree',
        priority: 0,
        localComponent: CoreEntityTree,
    }],
    'context-panel': [{
        id: 'core-context-panel',
        component: 'CoreContextPanel',
        priority: 0,
        localComponent: CoreContextPanel,
    }],
    'layer-toggle': [{
        id: 'core-layer-toggles',
        component: 'CoreLayerToggles',
        priority: 0,
        localComponent: CoreLayerToggles,
    }],
};

/** All bundled local modules with their slots - Now using centralized registry */
const LOCAL_MODULES: Record<string, ModuleViewerSlots> = {
    'core': CORE_MODULE_SLOTS,
    // Local modules are now loaded from the centralized registry
    ...getLocalModuleSlots(),
};

const LOCAL_MODULE_IDS: ReadonlySet<string> = new Set([
    ...Object.keys(LOCAL_MODULES),
    ...getAllLocalModuleIds(),
]);

// =============================================================================
// Slot Registry Context
// =============================================================================

interface SlotRegistryContextType {
    /** Get all widgets for a specific slot (from all active modules) */
    getWidgetsForSlot: (slot: SlotType) => SlotWidgetDefinition[];

    /** Get visible widgets based on current viewer state */
    getVisibleWidgets: (slot: SlotType) => SlotWidgetDefinition[];

    /** IDs of modules currently active in the viewer */
    activeModuleIds: Set<string>;

    /** Toggle a module's visibility in the viewer */
    toggleModule: (moduleId: string) => void;

    /** Activate a module */
    activateModule: (moduleId: string) => void;

    /** Deactivate a module */
    deactivateModule: (moduleId: string) => void;

    /** Check if a module is active */
    isModuleActive: (moduleId: string) => boolean;
}

const SlotRegistryContext = createContext<SlotRegistryContextType | undefined>(undefined);

interface SlotRegistryProviderProps {
    children: ReactNode;
    /**
     * Apply the per-module switches of the viewer's Layers panel (and the
     * defaults modules declare for it). Only the unified viewer sets this;
     * other hosts of slots (e.g. the dashboard) show every module.
     */
    respectViewerChoices?: boolean;
}

export const SlotRegistryProvider: React.FC<SlotRegistryProviderProps> = ({ children, respectViewerChoices = false }) => {
    const { modules } = useModules();
    // SlotRegistryProvider is always used within ViewerProvider (in UnifiedViewer)
    // So we can safely use useViewer() here
    const viewerContext = useViewer();

    const { tenantId } = useAuth();

    // Explicit on/off choices from the viewer's Layers panel, kept for the
    // session. Modules without a choice follow the default they declare.
    // Choices belong to one tenant; a tenant change swaps them during render
    // so no frame ever applies (or saves) another tenant's choices.
    const [choiceState, setChoiceState] = useState<{ tenantId: string; choices: ActivationChoices }>(
        () => ({ tenantId, choices: respectViewerChoices ? loadChoices(tenantId) : {} }),
    );
    let choices = choiceState.choices;
    if (choiceState.tenantId !== tenantId) {
        choices = respectViewerChoices ? loadChoices(tenantId) : {};
        setChoiceState({ tenantId, choices });
    }

    const activeModuleIds = useMemo(
        () => resolveActiveModules(
            respectViewerChoices
                ? modules
                : modules.map(m => ({ ...m, viewerDefaultActive: undefined })),
            choices,
            LOCAL_MODULE_IDS,
        ),
        [modules, choices, respectViewerChoices],
    );

    const setChoice = useCallback((moduleId: string, on: boolean) => {
        if (moduleId === 'core') return; // Core is always active
        setChoiceState(prev => ({ ...prev, choices: { ...prev.choices, [moduleId]: on } }));
    }, []);

    // Persist the viewer's choices for the session (the dashboard keeps its
    // in-memory toggles to itself).
    useEffect(() => {
        if (respectViewerChoices) saveChoices(choiceState.tenantId, choiceState.choices);
    }, [respectViewerChoices, choiceState]);

    const toggleModule = useCallback((moduleId: string) => {
        setChoice(moduleId, !activeModuleIds.has(moduleId));
    }, [setChoice, activeModuleIds]);

    const activateModule = useCallback((moduleId: string) => {
        setChoice(moduleId, true);
    }, [setChoice]);

    const deactivateModule = useCallback((moduleId: string) => {
        setChoice(moduleId, false);
    }, [setChoice]);

    const isModuleActive = useCallback((moduleId: string) => {
        return activeModuleIds.has(moduleId);
    }, [activeModuleIds]);

    // Get all widgets for a slot from active modules
    const getWidgetsForSlot = useCallback((slot: SlotType): SlotWidgetDefinition[] => {
        const widgets: SlotWidgetDefinition[] = [];
        const processedModuleIds = new Set<string>();

        // Add widgets from local bundled modules (core, etc.)
        // These take precedence because they have the actual React components
        Object.entries(LOCAL_MODULES).forEach(([moduleId, moduleSlots]) => {
            if (activeModuleIds.has(moduleId) && moduleSlots[slot]) {
                widgets.push(...moduleSlots[slot]!);
                processedModuleIds.add(moduleId);
            }
        });

        // Add widgets from remote modules loaded via ModuleContext
        // Skip modules that are already in LOCAL_MODULES to avoid duplication
        modules.forEach(module => {
            // Skip if this module is already processed from LOCAL_MODULES
            if (processedModuleIds.has(module.id)) {
                return;
            }

            const moduleSlots = module.viewerSlots?.[slot];
            if (activeModuleIds.has(module.id) && moduleSlots) {
                widgets.push(...moduleSlots);
            }
        });

        return widgets.sort((a, b) => a.priority - b.priority);
    }, [modules, activeModuleIds]);

    // Get visible widgets based on current viewer state
    const getVisibleWidgets = useCallback((slot: SlotType): SlotWidgetDefinition[] => {
        const allWidgets = getWidgetsForSlot(slot);

        return allWidgets.filter(widget => {
            if (!widget.showWhen) return true;

            const { entityType, layerActive } = widget.showWhen;

            if (entityType && entityType.length > 0) {
                if (!viewerContext.selectedEntityType) return false;
                if (!entityType.includes(viewerContext.selectedEntityType)) return false;
            }

            if (layerActive && layerActive.length > 0) {
                const hasActiveLayer = layerActive.some(layer =>
                    viewerContext.activeLayers.has(layer as any)
                );
                if (!hasActiveLayer) return false;
            }

            return true;
        });
    }, [getWidgetsForSlot, viewerContext.selectedEntityType, viewerContext.activeLayers]);

    const value = useMemo<SlotRegistryContextType>(() => ({
        getWidgetsForSlot,
        getVisibleWidgets,
        activeModuleIds,
        toggleModule,
        activateModule,
        deactivateModule,
        isModuleActive,
    }), [
        getWidgetsForSlot,
        getVisibleWidgets,
        activeModuleIds,
        toggleModule,
        activateModule,
        deactivateModule,
        isModuleActive,
    ]);

    return (
        <SlotRegistryContext.Provider value={value}>
            {children}
        </SlotRegistryContext.Provider>
    );
};

export const useSlotRegistry = (): SlotRegistryContextType => {
    const context = useContext(SlotRegistryContext);
    if (context === undefined) {
        throw new Error('useSlotRegistry must be used within a SlotRegistryProvider');
    }
    return context;
};

// Optional hook for components that may be outside the provider
export const useSlotRegistryOptional = (): SlotRegistryContextType | null => {
    return useContext(SlotRegistryContext) ?? null;
};

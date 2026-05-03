// =============================================================================
// Unified Viewer - Command Center Layout
// =============================================================================
// Full-screen viewer with persistent CesiumMap and collapsible overlay panels.
// Uses the Slot System to render widgets from modules dynamically.

import React, { useState, useEffect, useCallback, Suspense } from 'react';
import { CesiumMap } from '@/components/CesiumMap';
import { EntityWizard } from '@/components/EntityWizard';
import { PlacementToolbar } from '@/components/EntityWizard/PlacementToolbar';
import { ViewerHeader } from '@/components/viewer/ViewerHeader';
import { SlotRenderer } from '@/components/SlotRenderer';
import { MapToolbar } from '@/components/viewer/MapToolbar';
import { MapDrawingOverlay } from '@/components/viewer/MapDrawingOverlay';
import { ParcelForm } from '@/components/parcels/ParcelForm';
import { useViewer } from '@/context/ViewerContext';
import { SlotRegistryProvider } from '@/context/SlotRegistry';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useModules } from '@/context/ModuleContext';
import api from '@/services/api';
import { parcelApi } from '@/services/parcelApi';
import { cadastralApi } from '@/services/cadastralApi';
// Removed hardcoded vegetation layer data import - modules should use slot system
import { calculatePolygonAreaHectares } from '@/utils/geo';
import { logger } from '@/utils/logger';
import { ViewerKeyboardShortcuts } from '@/components/viewer/ViewerKeyboardShortcuts';
import { useRiskOverlay } from '@/hooks/cesium/useRiskOverlay';
import type { Robot, Sensor, Parcel, AgriculturalMachine, LivestockAnimal, WeatherStation, GeoPolygon } from '@/types';
import {
    Layers,
    X,
    Loader2,
    AlertTriangle,
} from 'lucide-react';
import { SidebarShell, TimelineShell } from '@nekazari/viewer-kit';

// Loading fallback for lazy-loaded content
const PanelLoadingFallback: React.FC = () => (
    <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-nkz-text-muted" />
    </div>
);

type SidebarState = 'closed' | 'compact' | 'expanded';

/** Inner viewer component that uses SlotRegistry */
const UnifiedViewerInner: React.FC = () => {
    const { hasAnyRole: _hasAnyRole } = useAuth();
    const { modules } = useModules();

    // Combined state logic for sidebar
    const {
        isLayerActive,
        isLeftPanelOpen,
        isRightPanelOpen,
        toggleLeftPanel,
        toggleRightPanel,
        currentDate,
        selectedEntityId,
        selectedEntityType: _selectedEntityType,
        mapMode,
        setMapMode,
        resetMapMode,
        pickingCallback,
        cancelPicking,
        drawingType,
        drawingCallback,
        selectEntity,
    } = useViewer();

    // Local expanded state for left panel
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    // Local expanded state for right panel
    const [isRightPanelExpanded, setIsRightPanelExpanded] = useState(false);

    // Derived current state for left panel
    const sidebarState: SidebarState = !isLeftPanelOpen ? 'closed' : isLeftPanelExpanded ? 'expanded' : 'compact';
    // Derived current state for right panel
    const rightSidebarState: SidebarState = !isRightPanelOpen ? 'closed' : isRightPanelExpanded ? 'expanded' : 'compact';

    // Vegetation layer removed - external modules handle this via slot system

    // Entity data for the map (still needed here for CesiumMap)
    const [robots, setRobots] = useState<Robot[]>([]);
    const [sensors, setSensors] = useState<Sensor[]>([]);
    const [parcels, setParcels] = useState<Parcel[]>([]);
    const [machines, setMachines] = useState<AgriculturalMachine[]>([]);
    const [livestock, setLivestock] = useState<LivestockAnimal[]>([]);
    const [weatherStations, setWeatherStations] = useState<WeatherStation[]>([]);
    const [crops, setCrops] = useState<any[]>([]);
    const [buildings, setBuildings] = useState<any[]>([]);
    const [trees, setTrees] = useState<any[]>([]); // OliveTree, AgriTree, FruitTree, Vine
    const [energyTrackers, setEnergyTrackers] = useState<any[]>([]); // AgriEnergyTracker

    // Risk overlay
    const { enabled: riskEnabled, setEnabled: setRiskEnabled, overlay: riskOverlay } = useRiskOverlay();

    // UI state
    const [_isLoading, setIsLoading] = useState(true);
    const [isWizardOpen, setIsWizardOpen] = useState(false);
    const [isLayerManagerOpen, setIsLayerManagerOpen] = useState(false);

    // Drawing state (for DRAW_PARCEL mode)
    const [drawnGeometry, setDrawnGeometry] = useState<GeoPolygon | null>(null);
    const [drawnArea, setDrawnArea] = useState<number | null>(null);
    const [cadastralData, setCadastralData] = useState<{
        reference: string;
        municipality?: string;
        province?: string;
        address?: string;
    } | null>(null);

    // Log modules for debugging
    useEffect(() => {
        logger.debug('[UnifiedViewer] Modules available:', modules?.length || 0);
        if (modules?.length > 0) {
            modules.forEach(m => {
                logger.debug(`  - ${m.name}: slots=`, m.viewerSlots ? Object.keys(m.viewerSlots) : 'none');
            });
        }
    }, [modules]);

    // Load all entities for the map
    const loadAllEntities = useCallback(async () => {
        setIsLoading(true);
        try {
            const results = await Promise.allSettled([
                api.getRobots().catch(() => []),
                api.getSensors().catch(() => []),
                api.getMachines().catch(() => []),
                api.getLivestock().catch(() => []),
                api.getWeatherStations().catch(() => []),
                parcelApi.getParcels().catch(() => []),
                api.getSDMEntityInstances('AgriCrop').catch(() => []),
                api.getSDMEntityInstances('AgriBuilding').catch(() => []),
                // Fetch tree/plant entities
                api.getSDMEntityInstances('OliveTree').catch(() => []),
                api.getSDMEntityInstances('AgriTree').catch(() => []),
                api.getSDMEntityInstances('FruitTree').catch(() => []),
                api.getSDMEntityInstances('Vine').catch(() => []),
                // Fetch sensors from NGSI-LD (created via EntityWizard)
                api.getSDMEntityInstances('AgriSensor').catch(() => []),
                // Energy trackers
                api.getSDMEntityInstances('AgriEnergyTracker').catch(() => []),
            ]);

            const [robotsRes, sensorsRes, machinesRes, livestockRes, weatherRes, parcelsRes, cropsRes, buildingsRes,
                oliveTreeRes, agriTreeRes, fruitTreeRes, vineRes, agriSensorRes, energyTrackersRes] = results;

            setRobots(robotsRes.status === 'fulfilled' ? robotsRes.value : []);
            // Combine sensors from PostgreSQL API and NGSI-LD (SDM)
            const pgSensors = sensorsRes.status === 'fulfilled' ? sensorsRes.value : [];
            const ngsiSensors = agriSensorRes.status === 'fulfilled' ? agriSensorRes.value : [];
            const allSensors = [...pgSensors, ...ngsiSensors];
            setSensors(allSensors);
            logger.debug('[UnifiedViewer] Sensors loaded:', allSensors.length, '(PG:', pgSensors.length, 'NGSI:', ngsiSensors.length, ')');
            setMachines(machinesRes.status === 'fulfilled' ? machinesRes.value : []);
            setLivestock(livestockRes.status === 'fulfilled' ? livestockRes.value : []);
            setWeatherStations(weatherRes.status === 'fulfilled' ? weatherRes.value : []);
            setParcels(parcelsRes.status === 'fulfilled' ? parcelsRes.value : []);
            setCrops(cropsRes.status === 'fulfilled' ? cropsRes.value : []);
            setBuildings(buildingsRes.status === 'fulfilled' ? buildingsRes.value : []);


            // Combine all tree types into one array
            const allTrees = [
                ...(oliveTreeRes.status === 'fulfilled' ? oliveTreeRes.value : []),
                ...(agriTreeRes.status === 'fulfilled' ? agriTreeRes.value : []),
                ...(fruitTreeRes.status === 'fulfilled' ? fruitTreeRes.value : []),
                ...(vineRes.status === 'fulfilled' ? vineRes.value : []),
            ];
            setTrees(allTrees);
            logger.debug('[UnifiedViewer] Trees loaded:', allTrees.length);

            setEnergyTrackers(energyTrackersRes.status === 'fulfilled' ? energyTrackersRes.value : []);

            logger.debug('[UnifiedViewer] Entities loaded for map');
        } catch (error) {
            logger.error('[UnifiedViewer] Error loading entities:', error);
        } finally {
            setIsLoading(false);
        }
    }, []);

    useEffect(() => {
        loadAllEntities();
    }, [loadAllEntities]);

    // Get selected entity data for the map
    const getSelectedEntityData = () => {
        if (!selectedEntityId) return undefined;

        // Search in all entity arrays
        const allEntities = [
            ...parcels.map(p => ({ ...p, _type: 'parcel' })),
            ...robots.map(r => ({ ...r, _type: 'robot' })),
            ...sensors.map(s => ({ ...s, _type: 'sensor' })),
            ...machines.map(m => ({ ...m, _type: 'machine' })),
            ...weatherStations.map(w => ({ ...w, _type: 'weather' })),
            ...livestock.map(l => ({ ...l, _type: 'livestock' })),
        ];

        return allEntities.find(e => e.id === selectedEntityId);
    };

    // Handle drawing completion (for DRAW_PARCEL mode)
    const handleDrawingComplete = useCallback((geometry: GeoPolygon, area: number | null) => {
        setDrawnGeometry(geometry);
        setDrawnArea(area);
        // Keep in DRAW_PARCEL mode - user can accept or cancel via toolbar
        // Form will be shown in right panel
    }, []);

    // Handle accept drawing (save parcel)
    const handleAcceptDrawing = useCallback(async () => {
        if (drawnGeometry) {
            // Form submission is handled by ParcelForm component
            // This will be called after form is submitted
            logger.debug('[UnifiedViewer] Drawing accepted, form will handle save');
        }
    }, [drawnGeometry]);

    // Handle cancel drawing
    const handleCancelDrawing = useCallback(() => {
        setDrawnGeometry(null);
        setDrawnArea(null);
        setCadastralData(null);
        resetMapMode();
    }, [resetMapMode]);

    // Handle map click for SELECT_CADASTRAL mode
    const handleMapClickForCadastral = useCallback(async (lat: number, lon: number) => {
        if (mapMode !== 'SELECT_CADASTRAL') return;

        try {
            logger.debug('[UnifiedViewer] Querying cadastral service:', { lon, lat });
            const cadastralData = await cadastralApi.queryByCoordinates(lon, lat);

            if (cadastralData.cadastralReference) {
                const hasGeometry = cadastralData.geometry &&
                    cadastralData.geometry.type === 'Polygon' &&
                    cadastralData.geometry.coordinates &&
                    cadastralData.geometry.coordinates.length > 0;

                if (hasGeometry && cadastralData.geometry) {
                    // Calculate area
                    const areaHectares = calculatePolygonAreaHectares(cadastralData.geometry);
                    setDrawnGeometry(cadastralData.geometry);
                    setDrawnArea(areaHectares);
                    setCadastralData({
                        reference: cadastralData.cadastralReference,
                        municipality: cadastralData.municipality,
                        province: cadastralData.province,
                        address: cadastralData.address,
                    });
                    // Switch to DRAW_PARCEL mode to show form
                    setMapMode('DRAW_PARCEL');
                } else {
                    // No geometry: show cadastral data and allow manual drawing
                    setCadastralData({
                        reference: cadastralData.cadastralReference,
                        municipality: cadastralData.municipality,
                        province: cadastralData.province,
                        address: cadastralData.address,
                    });
                    setMapMode('DRAW_PARCEL');
                }
            } else {
                alert('No se encontró información catastral para esta ubicación.');
            }
        } catch (error: any) {
            logger.error('[UnifiedViewer] Error querying cadastral:', error);
            const errorMsg = error.response?.data?.error || error.message || 'Error desconocido';
            alert(`Error al consultar el servicio catastral: ${errorMsg}`);
        }
    }, [mapMode, setMapMode]);

    // Handle map click for PICK_LOCATION mode
    const handleMapClickForPicking = useCallback((lat: number, lon: number) => {
        if (mapMode === 'PICK_LOCATION' && pickingCallback) {
            pickingCallback(lat, lon);
            // Reset to view mode (which will show the modal again)
            cancelPicking();
        }
    }, [mapMode, pickingCallback, cancelPicking]);

    // Handle generic drawing completion (for EntityWizard)
    const handleGenericDrawingComplete = useCallback((geometry: any, _area: number | null) => {
        if (drawingCallback) {
            drawingCallback(geometry);
        }
        resetMapMode();
    }, [drawingCallback, resetMapMode]);

    // Handle parcel form save
    const handleParcelFormSave = useCallback(async (data: Partial<Parcel>) => {
        if (!drawnGeometry) {
            logger.error('[UnifiedViewer] No geometry to save');
            return;
        }

        try {
            const geometry: GeoPolygon = {
                type: 'Polygon',
                coordinates: drawnGeometry.coordinates,
            };

            // Calculate municipality from geometry if not provided
            let municipality = data.municipality || cadastralData?.municipality;
            let province = data.province || cadastralData?.province;

            if (geometry.coordinates && geometry.coordinates[0] && geometry.coordinates[0].length > 0) {
                const ring = geometry.coordinates[0];
                let sumLon = 0;
                let sumLat = 0;
                let count = 0;

                ring.forEach((point: number[]) => {
                    if (point && point.length >= 2 && typeof point[0] === 'number' && typeof point[1] === 'number') {
                        sumLon += point[0];
                        sumLat += point[1];
                        count++;
                    }
                });

                if (count > 0 && (!municipality || municipality === 'Abaigar')) {
                    const centroid = { lat: sumLat / count, lon: sumLon / count };
                    try {
                        const municipalityData = await api.getNearestMunicipality(centroid.lat, centroid.lon, 10);
                        if (municipalityData && municipalityData.municipality) {
                            municipality = municipalityData.municipality.name;
                            province = municipalityData.municipality.province || province;
                        }
                    } catch (err) {
                        logger.error('[UnifiedViewer] Error calculating municipality:', err);
                    }
                }
            }

            const newParcel: Partial<Parcel> = {
                name: data.name,
                geometry: geometry,
                area: drawnArea || 0,
                cropType: data.cropType,
                municipality: municipality || '',
                province: province || '',
                notes: data.notes,
                cadastralReference: cadastralData?.reference || data.cadastralReference,
                category: 'cadastral',
            };

            await parcelApi.createParcel(newParcel);
            logger.debug('[UnifiedViewer] Parcel created successfully');

            // Reload parcels
            await loadAllEntities();

            // Reset state
            handleCancelDrawing();
        } catch (error: any) {
            logger.error('[UnifiedViewer] Error creating parcel:', error);
            const errorMessage = error.response?.data?.detail || error.response?.data?.error || error.message || 'Error al guardar la parcela';
            alert(errorMessage);
            throw error;
        }
    }, [drawnGeometry, drawnArea, cadastralData, loadAllEntities, handleCancelDrawing]);


    // Handle map entity selection
    const handleEntityMapSelect = useCallback((entity: { id: string; type: string }) => {
        logger.debug('[UnifiedViewer] Map entity selected:', entity);
        selectEntity(entity.id, entity.type);
    }, [selectEntity]);

    return (
        <div className="fixed inset-0 w-full h-full overflow-hidden bg-slate-900">
            {/* Global keyboard shortcut listener */}
            <ViewerKeyboardShortcuts />

            {/* Floating Header - Logo with dropdown menu + controls; right strip includes Layers + Theme + Language */}
            <ViewerHeader
                rightContent={
                    <button
                        type="button"
                        onClick={() => setIsLayerManagerOpen(!isLayerManagerOpen)}
                        className="p-2.5 rounded-nkz-lg bg-nkz-surface border border-nkz-border hover:bg-nkz-surface-raised transition-all shadow-nkz-lg"
                        title="Gestionar capas"
                        aria-label="Gestionar capas"
                    >
                        <Layers className="w-5 h-5 text-slate-700 dark:text-slate-200" />
                    </button>
                }
            />

            {/* Map Toolbar - Contextual toolbar for drawing/editing modes */}
            {mapMode === 'PICK_LOCATION' && (
                <div className="absolute top-20 left-1/2 transform -translate-x-1/2 z-50">
                    <div className="bg-nkz-surface-raised border border-nkz-border shadow-nkz-lg px-nkz-section py-nkz-stack rounded-nkz-full flex items-center gap-nkz-stack">
                        <p className="text-nkz-text-primary font-medium">Haga clic en el mapa para seleccionar ubicación</p>
                        <button
                            onClick={cancelPicking}
                            className="bg-slate-200 hover:bg-slate-300 text-slate-700 px-3 py-1 rounded-full text-sm font-medium transition-colors"
                        >
                            Cancelar
                        </button>
                    </div>
                </div>
            )}

            <MapToolbar
                onAccept={mapMode === 'DRAW_PARCEL' ? handleAcceptDrawing : undefined}
                onCancel={handleCancelDrawing}
                onUndo={mapMode === 'DRAW_PARCEL' ? () => logger.debug('Undo') : undefined}
                onClear={mapMode === 'DRAW_PARCEL' ? () => {
                    setDrawnGeometry(null);
                    setDrawnArea(null);
                } : undefined}
            />

            {/* CesiumMap - Full background (no padding, header floats over) */}
            <div className="absolute inset-0">
                <CesiumMap
                    title=""
                    height="h-full"
                    showControls={true}
                    renderMapLayerSlot={false}
                    parcels={isLayerActive('parcels') ? parcels : []}
                    robots={isLayerActive('robots') ? robots : []}
                    sensors={isLayerActive('sensors') ? sensors : []}
                    machines={isLayerActive('machines') ? machines : []}
                    livestock={isLayerActive('livestock') ? livestock : []}
                    weatherStations={isLayerActive('weather') ? weatherStations : []}
                    crops={isLayerActive('crops') ? crops : []}
                    buildings={isLayerActive('buildings') ? buildings : []}
                    trees={trees} // Always show trees (OliveTree, AgriTree, etc.)
                    energyTrackers={energyTrackers} // AgriEnergyTracker (solar panels)
                    enable3DTerrain={true}
                    terrainProvider="auto"
                    selectedEntity={getSelectedEntityData()}
                    // vegetationLayerConfig removed - modules use slot system
                    // Disable entity selection when in drawing/editing/placement modes.
                    // 'picker' mode disables entity selection click handler, preventing
                    // interference with drawing overlays (MapDrawingOverlay).
                    mode={mapMode === 'VIEW' ? 'view' : 'picker'}
                    onMapClick={mapMode === 'SELECT_CADASTRAL' ? handleMapClickForCadastral : mapMode === 'PICK_LOCATION' ? handleMapClickForPicking : undefined}
                    onEntitySelect={handleEntityMapSelect}
                    riskOverlay={riskOverlay}
                />

                {/* Map Layer Slot - Dynamic widgets overlaying the map (Search, Controls, etc.) */}
                <div className="absolute inset-0 pointer-events-none z-10">
                    <Suspense fallback={null}>
                        <SlotRenderer slot="map-layer" className="w-full h-full pointer-events-none [&>*]:pointer-events-auto" inline={false} />
                    </Suspense>
                </div>

                {/* Drawing Overlay - Only active in DRAW_PARCEL mode */}
                {mapMode === 'DRAW_PARCEL' && (
                    <MapDrawingOverlay
                        enabled={true}
                        onComplete={handleDrawingComplete}
                        onCancel={handleCancelDrawing}
                    />
                )}
                {/* Generic Drawing Overlay - Active in DRAW_GEOMETRY mode */}
                {mapMode === 'DRAW_GEOMETRY' && (
                    <MapDrawingOverlay
                        enabled={true}
                        drawingType={drawingType || 'Polygon'}
                        onComplete={handleGenericDrawingComplete}
                        onCancel={resetMapMode}
                    />
                )}
            </div>

            {/* Layer Manager Dropdown - below the header right strip */}
            {isLayerManagerOpen && (
                <div className="absolute top-16 right-4 z-nkz-popover w-72 rounded-nkz-lg bg-nkz-surface border border-nkz-border shadow-nkz-lg overflow-hidden">
                    <div className="flex items-center justify-between px-nkz-stack py-nkz-inline border-b border-nkz-border">
                        <h3 className="text-nkz-sm font-semibold text-nkz-text-primary flex items-center gap-nkz-inline">
                            <Layers className="w-4 h-4" />
                            Capas
                        </h3>
                        <button onClick={() => setIsLayerManagerOpen(false)} className="text-nkz-text-muted hover:text-nkz-text-primary">
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                    <div className="p-nkz-tight">
                        {/* Risk overlay toggle */}
                        <button
                            type="button"
                            onClick={() => setRiskEnabled(!riskEnabled)}
                            className={`w-full flex items-center gap-nkz-inline px-nkz-inline py-nkz-tight rounded-nkz-sm text-nkz-sm transition-colors ${
                                riskEnabled ? 'bg-nkz-danger-soft text-nkz-danger border border-nkz-danger' : 'text-nkz-text-secondary hover:bg-nkz-surface-sunken'
                            }`}
                        >
                            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                            <span>Overlay de Riesgos</span>
                            <span className={`ml-auto text-nkz-xs px-nkz-tight py-0.5 rounded-nkz-sm ${riskEnabled ? 'bg-nkz-danger-soft text-nkz-danger-strong' : 'bg-nkz-surface-sunken text-nkz-text-muted'}`}>
                                {riskEnabled ? 'ON' : 'OFF'}
                            </span>
                        </button>
                        <Suspense fallback={<PanelLoadingFallback />}>
                            <SlotRenderer slot="layer-toggle" />
                        </Suspense>
                    </div>
                </div>
            )}

            {/* Left Panel - Entity Tree (uses SidebarShell) */}
            <SidebarShell
                side="left"
                state={sidebarState}
                onStateChange={(s: SidebarState) => {
                    if (s === 'closed') { toggleLeftPanel(); setIsLeftPanelExpanded(false); }
                    else if (s === 'compact') { if (!isLeftPanelOpen) toggleLeftPanel(); setIsLeftPanelExpanded(false); }
                    else { if (!isLeftPanelOpen) toggleLeftPanel(); setIsLeftPanelExpanded(true); }
                }}
            >
                <SidebarShell.Pinned>
                    <Suspense fallback={<PanelLoadingFallback />}>
                        <SlotRenderer
                            slot="entity-tree"
                            className="flex-1 flex flex-col"
                            additionalProps={{ onAddEntity: () => setIsWizardOpen(true) }}
                        />
                    </Suspense>
                </SidebarShell.Pinned>
            </SidebarShell>

            {/* Right Panel - Context/Details (uses SidebarShell) */}
            <SidebarShell
                side="right"
                state={rightSidebarState}
                onStateChange={(s: SidebarState) => {
                    if (s === 'closed') { toggleRightPanel(); setIsRightPanelExpanded(false); }
                    else if (s === 'compact') { if (!isRightPanelOpen) toggleRightPanel(); setIsRightPanelExpanded(false); }
                    else { if (!isRightPanelOpen) toggleRightPanel(); setIsRightPanelExpanded(true); }
                }}
            >
                {/* Show ParcelForm when in DRAW_PARCEL mode with drawn geometry */}
                {mapMode === 'DRAW_PARCEL' && drawnGeometry ? (
                    <div className="flex-1 overflow-y-auto p-nkz-stack">
                        <h3 className="text-nkz-lg font-semibold text-nkz-text-primary mb-nkz-stack">Nueva Parcela</h3>
                        {cadastralData && (
                            <p className="text-nkz-sm text-nkz-text-secondary mb-nkz-tight">
                                Datos catastrales: {cadastralData.reference}
                            </p>
                        )}
                        {drawnArea && (
                            <p className="text-nkz-sm text-nkz-text-secondary">
                                Área: {drawnArea.toFixed(2)} ha
                            </p>
                        )}
                        <ParcelForm
                            initialData={cadastralData ? {
                                id: '',
                                name: cadastralData.reference,
                                cadastralReference: cadastralData.reference,
                                municipality: cadastralData.municipality || '',
                                province: cadastralData.province || '',
                                cropType: '',
                                notes: '',
                                area: drawnArea || 0,
                                geometry: drawnGeometry,
                            } as Parcel : null}
                            geometry={drawnGeometry}
                            onSave={handleParcelFormSave}
                            onCancel={handleCancelDrawing}
                            mode="create"
                        />
                    </div>
                ) : (
                    <Suspense fallback={<PanelLoadingFallback />}>
                        <div className="flex-1 min-h-0 overflow-y-auto p-nkz-stack">
                            <SlotRenderer
                                slot="context-panel"
                                className="flex flex-col gap-nkz-stack"
                                additionalProps={{ entityData: getSelectedEntityData() }}
                                resetKeys={selectedEntityId ? [selectedEntityId] : []}
                            />
                        </div>
                    </Suspense>
                )}
            </SidebarShell>

            {/* Bottom Panel - Timeline (uses TimelineShell) */}
            <div
                className="absolute right-4 bottom-0 z-30 transition-all duration-500 ease-in-out"
                style={{
                    left: sidebarState === 'expanded' ? '650px' : sidebarState === 'compact' ? '380px' : '16px',
                    marginRight: rightSidebarState === 'expanded' ? '600px' : rightSidebarState === 'compact' ? '400px' : '0px',
                }}
            >
                <TimelineShell
                    startTime={Date.now() - 7 * 24 * 3600 * 1000}
                    endTime={Date.now() + 24 * 3600 * 1000}
                    cursor={currentDate.getTime()}
                    onCursorChange={(_t: number) => { /* ViewerContext doesn't expose setCurrentDate - no-op */ }}
                    forecastFrom={Date.now()}
                    snapping="day"
                    variant="docked"
                />
            </div>

            {/* Entity Wizard Modal */}
            <EntityWizard
                isOpen={isWizardOpen}
                onClose={() => setIsWizardOpen(false)}
                onSuccess={() => {
                    loadAllEntities();
                    setIsWizardOpen(false);
                }}
            />

            {/* 3D Model Placement Toolbar - Floating action bar */}
            <PlacementToolbar
                onConfirm={() => {
                    // Reload entities after placement
                    loadAllEntities();
                }}
                onCancel={() => {
                    // Nothing extra needed, context handles state reset
                }}
            />
        </div >
    );
};

/** Main UnifiedViewer with SlotRegistryProvider wrapper */
export const UnifiedViewer: React.FC = () => {
    return (
        <SlotRegistryProvider>
            <UnifiedViewerInner />
        </SlotRegistryProvider>
    );
};

export default UnifiedViewer;

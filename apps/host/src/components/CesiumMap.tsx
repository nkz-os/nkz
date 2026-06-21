// =============================================================================
// Cesium Map Component - GeoServer Integration
// =============================================================================

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
/* eslint-disable @typescript-eslint/no-explicit-any */
  Maximize2,
  Minimize2,
  Layers,
  Map as MapIcon,
  Mountain
} from 'lucide-react';
import { Robot, Sensor, Parcel, AgriculturalMachine, LivestockAnimal, WeatherStation, AgriCrop, AgriBuilding, Device } from '@/types';
import { FieldPhotoRecord } from '@/utils/fieldPhotos';
import { useViewerOptional } from '@/context/ViewerContext';
import { SlotRenderer } from '@/components/SlotRenderer';
import { CesiumStampRenderer } from '@/components/CesiumStampRenderer';
import { useTerrainProvider } from '@/hooks/cesium/useTerrainProvider';
import { useRegionResolver } from '@/hooks/useRegionResolver';
import { imageryProviderForRegion } from '@/utils/terrain';
import type { RegionId } from '@/utils/regions';
import { use3DTiles } from '@/hooks/cesium/use3DTiles';
import { useEntitySelection } from '@/hooks/cesium/useEntitySelection';
import { useFlyToEntity } from '@/hooks/cesium/useFlyToEntity';
import { useModelPreview } from '@/hooks/cesium/useModelPreview';
import { logger } from '@/utils/logger';
import { normalizeAssetUrl } from '@/utils/urlNormalizer';
import { getEntityCoordinates, getEntityGeometryType } from '@/utils/ngsiEntityCoordinates';
import type { RiskOverlayInfo } from '@/hooks/cesium/useRiskOverlay';
import { MapSearchLupa } from '@/components/MapSearchLupa';
import { flyToForResult } from '@/utils/cameraFraming';
import { applyInitialFraming } from '@/hooks/useInitialCameraFraming';
import { parcelCentroid } from '@/types/geocode';
import { parcelApi } from '@/services/parcelApi';
// Removed hardcoded vegetation layer import - modules should use slot system

const RISK_SEVERITY_COLORS: Record<RiskOverlayInfo['severity'], string> = {
  critical: '#ef4444',
  high:     '#f97316',
  medium:   '#eab308',
  low:      '#22c55e',
};

/** DOM event from DataHub uPlot cursor (must match DATAHUB_EVENT_TIME_HOVER in nkz-module-datahub). */
const DATAHUB_EVENT_TIME_HOVER = 'nekazari:datahub:timeHover';

// Import Cesium CSS
import 'cesium/Build/Cesium/Widgets/widgets.css';
import { Button } from '@nekazari/ui-kit';

interface CesiumMapProps {
  title?: string;
  height?: string | number;
  showControls?: boolean;
  robots?: Robot[];
  sensors?: Sensor[];
  parcels?: Parcel[];
  machines?: AgriculturalMachine[];
  livestock?: LivestockAnimal[];
  weatherStations?: WeatherStation[];
  crops?: AgriCrop[];
  buildings?: AgriBuilding[];
  trees?: any[]; // OliveTree, AgriTree, FruitTree, Vine - generic SDM entities with 3D models
  energyTrackers?: any[]; // AgriEnergyTracker - solar trackers with MultiPoint geometry
  devices?: Device[];
  enable3DTerrain?: boolean; // Enable 3D terrain
  terrainProvider?: 'idena' | 'ign' | 'auto' | string; // Terrain provider: 'idena' (Navarra), 'ign' (España), 'auto' (detect by location), or custom URL
  enable3DTiles?: boolean; // Enable 3D Tiles layer (buildings, terrain)
  tilesetUrl?: string; // Optional: specific 3D Tiles tileset URL (default: Navarra)
  selectedEntity?: any; // Selected entity to zoom to
  mode?: 'view' | 'picker'; // Map mode: 'view' (default) or 'picker' (for selecting location)
  onMapClick?: (lat: number, lon: number) => void; // Callback for map clicks in picker mode
  onEntitySelect?: (entity: { id: string; type: string }) => void; // Callback when an entity is clicked
  riskOverlay?: Map<string, RiskOverlayInfo>; // Optional: risk severity colors keyed by entity ID
  renderMapLayerSlot?: boolean; // Whether to render map-layer slot inside this component
  fieldPhotos?: FieldPhotoRecord[];
  // Module layer configurations (extensible for future modules)
  // Removed vegetationLayerConfig - modules should register layers via slot system
}

// Map icon keys to SVG data URIs for Cesium billboards
// These are simplified versions of Lucide icons optimized for map display
const ICON_KEY_TO_SVG: Record<string, string> = {
  // Sensors
  'thermometer': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z"/></svg>`,
  'gauge': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>`,
  'activity': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>`,
  'radio': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/></svg>`,
  'wifi': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13a10 10 0 0 1 14 0"/><path d="M8.5 16.5a5 5 0 0 1 7 0"/><path d="M2 8.82a15 15 0 0 1 20 0"/><line x1="12" x2="12.01" y1="20" y2="20"/></svg>`,
  'camera': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/></svg>`,
  // Agriculture  
  'leaf': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/></svg>`,
  'sprout': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 20h10"/><path d="M10 20c5.5-2.5.8-6.4 3-10"/><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z"/><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z"/></svg>`,
  'trees': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 10v.2A3 3 0 0 1 8.9 16v0H5v0h0a3 3 0 0 1-1-5.8V10a3 3 0 0 1 6 0Z"/><path d="M7 16v6"/><path d="M13 19v3"/><path d="M12 19h8.3a1 1 0 0 0 .7-1.7L18 14h.3a1 1 0 0 0 .7-1.7L16 9h.2a1 1 0 0 0 .8-1.7L13 3l-1.4 1.5"/></svg>`,
  // Weather
  'sun': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>`,
  'wind': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.7 7.7a2.5 2.5 0 1 1 1.8 4.3H2"/><path d="M9.6 4.6A2 2 0 1 1 11 8H2"/><path d="M12.6 19.4A2 2 0 1 0 14 16H2"/></svg>`,
  'cloudrain': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="M16 14v6"/><path d="M8 14v6"/><path d="M12 16v6"/></svg>`,
  // Infrastructure
  'building': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4"/><path d="M10 10h4"/><path d="M10 14h4"/><path d="M10 18h4"/></svg>`,
  'warehouse': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 8.35V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.35A2 2 0 0 1 3.26 6.5l8-3.2a2 2 0 0 1 1.48 0l8 3.2A2 2 0 0 1 22 8.35Z"/><path d="M6 18h12"/><path d="M6 14h12"/><rect width="12" height="12" x="6" y="10"/></svg>`,
  // Fleet
  'bot': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>`,
  'tractor': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h9l1 7"/><path d="M4 11V4"/><path d="M8 10V4"/><path d="M18 5c-.6 0-1 .4-1 1v5.6"/><path d="m10 11 11 .9c.6 0 .9.5.8 1.1l-.8 5h-1"/><circle cx="7" cy="15" r="3"/><circle cx="17" cy="18" r="3"/></svg>`,
  // Energy
  'zap': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  // Water
  'droplets': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z"/><path d="M12.56 6.6A10.97 10.97 0 0 0 14 3.02c.5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a6.98 6.98 0 0 1-11.91 4.97"/></svg>`,
  // Generic
  'circledot': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/></svg>`,
  'mappin': `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>`,
};

// Convert icon key (like 'icon:thermometer') to data URI
const getIconDataUri = (iconKeyOrUrl: string): string | null => {
  if (!iconKeyOrUrl) return null;

  // If it's already a valid URL, return as-is
  if (iconKeyOrUrl.startsWith('http') || iconKeyOrUrl.startsWith('data:') || iconKeyOrUrl.startsWith('/')) {
    return iconKeyOrUrl;
  }

  // Handle icon key format (e.g., 'icon:thermometer')
  if (iconKeyOrUrl.startsWith('icon:')) {
    const key = iconKeyOrUrl.replace('icon:', '');
    const svg = ICON_KEY_TO_SVG[key];
    if (svg) {
      // Convert SVG to data URI with colored background circle
      const wrappedSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40"><circle cx="20" cy="20" r="18" fill="#0891b2"/><g transform="translate(4,4)">${svg.replace(/<svg[^>]*>/, '').replace('</svg>', '')}</g></svg>`;
      return `data:image/svg+xml;base64,${btoa(wrappedSvg)}`;
    }
  }

  return null;
};

// Entity category helpers — extracted from the monolithic effect so each
// per-category effect can reuse them without closure dependencies.

const getRobotColor = (Cesium: any, status?: string): any => {
  switch (status) {
    case 'working':     return Cesium.Color.GREEN;
    case 'idle':        return Cesium.Color.YELLOW;
    case 'charging':    return Cesium.Color.BLUE;
    case 'error':       return Cesium.Color.RED;
    case 'maintenance': return Cesium.Color.ORANGE;
    default:            return Cesium.Color.WHITE;
  }
};

const getMachineColor = (Cesium: any, operationType?: string): any => {
  switch (operationType) {
    case 'seeding':       return Cesium.Color.GREEN;
    case 'fertilization': return Cesium.Color.BLUE;
    case 'spraying':      return Cesium.Color.ORANGE;
    case 'harvesting':    return Cesium.Color.YELLOW;
    case 'tillage':       return Cesium.Color.BROWN;
    case 'irrigation':    return Cesium.Color.AQUA;
    default:              return Cesium.Color.GRAY;
  }
};

const getLivestockColor = (Cesium: any, activity?: string): any => {
  switch (activity) {
    case 'grazing': return Cesium.Color.GREEN;
    case 'resting': return Cesium.Color.BLUE;
    case 'moving':  return Cesium.Color.ORANGE;
    case 'feeding': return Cesium.Color.YELLOW;
    default:        return Cesium.Color.WHITE;
  }
};

const getEntityModelUrl = (entity: any, defaultModel?: string): string | undefined => {
  if (entity.ref3DModel?.value) return entity.ref3DModel.value;
  if (entity.ref3DModel && typeof entity.ref3DModel === 'string') return entity.ref3DModel;
  if (entity.model3d) return entity.model3d;
  if (entity.model3DUrl) return entity.model3DUrl;
  return defaultModel;
};

const resolveEntityIconUrl = (entity: any, defaultIcon: string): string | null => {
  let iconValue: string | null = null;
  if (entity.icon2d?.value) {
    iconValue = entity.icon2d.value;
  } else if (typeof entity.icon2d === 'string') {
    iconValue = entity.icon2d;
  }
  if (iconValue) {
    const dataUri = getIconDataUri(iconValue);
    if (dataUri) return dataUri;
  }
  return getIconDataUri(defaultIcon);
};

/** Fallback UI shown when the browser cannot create a WebGL context */
const WebGLFallback: React.FC = () => {
  const isFirefox = navigator.userAgent.includes('Firefox');
  const isChrome = navigator.userAgent.includes('Chrome');

  return (
    <div className="flex flex-col items-center justify-center h-full bg-slate-900 text-slate-300 p-8 gap-4">
      <MapIcon className="w-16 h-16 text-slate-500" />
      <h3 className="text-lg font-semibold text-white">Mapa 3D no disponible</h3>
      <p className="text-sm text-center max-w-md text-slate-400">
        Tu navegador no tiene WebGL activado, que es necesario para el visor 3D.
      </p>

      <div className="bg-slate-800 rounded-lg p-4 text-sm max-w-md w-full space-y-3 border border-slate-700">
        {isFirefox && (
          <>
            <p className="font-medium text-amber-400">Firefox — pasos para activar WebGL:</p>
            <ol className="list-decimal list-inside space-y-1 text-slate-300">
              <li>Escribe <code className="bg-slate-700 px-1 rounded">about:config</code> en la barra de direcciones</li>
              <li>Busca <code className="bg-slate-700 px-1 rounded">webgl.disabled</code> → ponlo a <strong>false</strong></li>
              <li>Busca <code className="bg-slate-700 px-1 rounded">webgl.enable-webgl2</code> → ponlo a <strong>true</strong></li>
              <li>Busca <code className="bg-slate-700 px-1 rounded">WebglAllowWindowsNativeGl</code> → ponlo a <strong>true</strong></li>
              <li>Recarga la página (no hace falta reiniciar Firefox)</li>
            </ol>
          </>
        )}
        {isChrome && (
          <>
            <p className="font-medium text-amber-400">Chrome — pasos para activar WebGL:</p>
            <ol className="list-decimal list-inside space-y-1 text-slate-300">
              <li>Escribe <code className="bg-slate-700 px-1 rounded">chrome://flags</code> en la barra de direcciones</li>
              <li>Busca &quot;WebGL&quot; y activa las opciones</li>
              <li>Reinicia Chrome</li>
            </ol>
          </>
        )}
        {!isFirefox && !isChrome && (
          <p className="text-slate-300">Prueba con un navegador diferente (Chrome, Edge) o comprueba la configuración de aceleración por hardware de tu navegador.</p>
        )}

        <p className="text-xs text-slate-500 pt-2 border-t border-slate-700">
          Tambien puedes comprobar tu GPU en{' '}
          {isFirefox
            ? <code className="bg-slate-700 px-1 rounded">about:support</code>
            : <code className="bg-slate-700 px-1 rounded">chrome://gpu</code>
          }
          {' '}→ Graphics
        </p>
      </div>
    </div>
  );
};

export const CesiumMap = React.memo<CesiumMapProps>(({
  showControls = true,
  robots = [],
  sensors = [],
  parcels = [],
  machines = [],
  livestock = [],
  weatherStations = [],
  crops = [],
  buildings = [],
  trees = [],
  energyTrackers = [],
  devices = [],
  enable3DTerrain = true, // Enable by default
  terrainProvider = 'auto', // Auto-detect based on location
  enable3DTiles = true, // Enable by default for Navarra tileset
  tilesetUrl = 'https://idena.navarra.es/3dtiles/Pamplona2025/tileset.json', // Default: Navarra 3D Tiles
  selectedEntity,
  mode = 'view',
  onMapClick,
  onEntitySelect,
  riskOverlay,
  renderMapLayerSlot = true,
  fieldPhotos = [],
  // vegetationLayerConfig removed - modules use slot system
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const initAttemptedRef = useRef(false); // prevent double-init on remount
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isViewerReady, setIsViewerReady] = useState(false);
  const [webglFailed, setWebglFailed] = useState(false);
  const [showTerrainPicker, setShowTerrainPicker] = useState(false);
  const [currentTerrainProvider, setCurrentTerrainProvider] = useState<string>(terrainProvider);
  const [baseLayer, setBaseLayer] = useState<'pnoa' | 'osm' | 'esri' | 'cesium'>('esri');
  const [currentRegion, setCurrentRegion] = useState<RegionId>('eu');
  const [layerAutoMode, setLayerAutoMode] = useState(true);
  const manualPickRef = useRef(false); // true when user clicked a layer button
  const osmLayerRef = useRef<any>(null);
  const pnoaLayerRef = useRef<any>(null);
  const esriLayerRef = useRef<any>(null);
  const cesiumLayerRef = useRef<any>(null);
  const baseLayerRef = useRef(baseLayer); // ref to avoid stale closures in async .then() callbacks
  const viewerContext = useViewerOptional();
  const setCesiumViewer = viewerContext?.setCesiumViewer;

  // Per-category entity tracking refs — each effect manages only its own entities,
  // eliminating the full-scene rebuild (removeAll + re-add) that previously
  // caused visible flicker and frame drops on every data update.
  const entityRefs = useRef({
    robot:      new Map<string, any>(),
    sensor:     new Map<string, any>(),
    machine:    new Map<string, any>(),
    livestock:  new Map<string, any>(),
    weather:    new Map<string, any>(),
    crop:       new Map<string, any>(),
    building:   new Map<string, any>(),
    device:     new Map<string, any>(),
    tree:       new Map<string, any>(),
    tracker:    new Map<string, any>(),
    parcel:     new Map<string, any>(),
    fieldPhoto: new Map<string, any>(),
  });

  // Update local state if prop changes
  useEffect(() => {
    setCurrentTerrainProvider(terrainProvider);
  }, [terrainProvider]);

  // Sync baseLayerRef so async callbacks (Esri, Cesium Ion .then()) see current value
  useEffect(() => {
    baseLayerRef.current = baseLayer;
  }, [baseLayer]);

  // Region resolver: camera.moveEnd → resolve region
  const handleRegionChange = useCallback((region: RegionId) => {
    setCurrentRegion(region);
    // Update __nkzRegion IMMEDIATELY on the viewer object so the
    // eu-elevation module's moveEnd listener sees current region
    // (avoiding the async gap from React useEffect batching).
    if (viewerRef.current) {
      const existing = (viewerRef.current as Record<string, unknown>).__nkzRegion as
        | { currentRegion: string; layerAutoMode: boolean }
        | undefined;
      (viewerRef.current as Record<string, unknown>).__nkzRegion = {
        currentRegion: region,
        layerAutoMode: existing?.layerAutoMode ?? true,
      };
    }
    // If user manually picked a layer, don't auto-switch
    if (manualPickRef.current) return;
  }, []);

  useRegionResolver(isViewerReady ? viewerRef.current : null, handleRegionChange);

  // Auto-switch imagery based on region
  useEffect(() => {
    if (!layerAutoMode || manualPickRef.current) return;
    const target = imageryProviderForRegion(currentRegion);
    // Only switch if it's changing between pnoa and esri
    if (target === 'pnoa' && baseLayer !== 'pnoa' && baseLayer !== 'osm') {
      setBaseLayer('pnoa');
    } else if (target === 'esri' && baseLayer !== 'esri' && baseLayer !== 'cesium') {
      setBaseLayer('esri');
    }
  }, [currentRegion, layerAutoMode, baseLayer]);

  // Sync __nkzRegion.layerAutoMode for cross-repo consumption
  useEffect(() => {
    if (!viewerRef.current) return;
    const existing = (viewerRef.current as Record<string, unknown>).__nkzRegion as
      | { currentRegion: string; layerAutoMode: boolean }
      | undefined;
    (viewerRef.current as Record<string, unknown>).__nkzRegion = {
      currentRegion: existing?.currentRegion ?? currentRegion,
      layerAutoMode,
    };
  }, [layerAutoMode, currentRegion]);

  useEffect(() => {
    if (!containerRef.current || viewerRef.current || webglFailed || initAttemptedRef.current) return;
    initAttemptedRef.current = true;

    logger.debug('[CesiumMap] Initializing Cesium viewer');

    try {
      // @ts-ignore - Cesium types
      const Cesium = window.Cesium;

      if (!Cesium) {
        logger.warn('[CesiumMap] Cesium not available, skipping initialization');
        return;
      }

      logger.debug('[CesiumMap] Creating Cesium viewer');

      // Initialize Cesium Viewer with error handling
      let viewer: any;
      try {
        viewer = new Cesium.Viewer(containerRef.current, {
          animation: false,
          timeline: false,
          vrButton: false,
          geocoder: false,
          homeButton: showControls,
          sceneModePicker: showControls,
          navigationHelpButton: false,
          baseLayerPicker: false,
          fullscreenButton: false,
          infoBox: false,
          selectionIndicator: false,
          requestRenderMode: true,
          maximumRenderTimeChange: Infinity,
          imageryProvider: false, // No default ION imagery — we add OSM manually below
          terrainProvider: new Cesium.EllipsoidTerrainProvider(),
          orderIndependentTranslucency: false,
          shadows: false,
          contextOptions: {
            requestWebgl1: true,
            webgl: {
              failIfMajorPerformanceCaveat: false,
            },
          },
        });

        // Hide Cesium ION credits completely
        if (viewer.cesiumWidget?.creditContainer) {
          viewer.cesiumWidget.creditContainer.style.display = 'none';
        }
        // Also hide credit container in the bottom-right
        const creditContainer = document.querySelector('.cesium-widget-credits');
        if (creditContainer) {
          (creditContainer as HTMLElement).style.display = 'none';
        }

        viewer.scene.backgroundColor = Cesium.Color.fromCssColorString('#0f172a');
        viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#1f2937');
        viewer.scene.globe.depthTestAgainstTerrain = true;
        if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = false;
        if (viewer.scene.sun) viewer.scene.sun.show = false;
        if (viewer.scene.moon) viewer.scene.moon.show = false;

        // Configure imagery provider IMMEDIATELY after creating viewer
        // Remove default imagery layer and add OSM as default (no Cesium ION)
        try {
          viewer.imageryLayers.removeAll();

          // Add OSM (OpenStreetMap)
          const osmProvider = new Cesium.UrlTemplateImageryProvider({
            url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            maximumLevel: 19,
            credit: 'OpenStreetMap Contributors',
          });
          const osmLayer = viewer.imageryLayers.addImageryProvider(osmProvider, 0); // Bottom
          osmLayerRef.current = osmLayer;
          logger.debug('[CesiumMap] Initial imagery provider (OSM) configured');

          // Add PNOA (Plan Nacional de Ortofotografía Aérea) as base layer option.
          // Added above OSM (index 1) so PNOA renders on top when visible.
          try {
            const pnoaProvider = new Cesium.WebMapServiceImageryProvider({
              url: 'https://www.ign.es/wms-inspire/pnoa-ma',
              layers: 'OI.OrthoimageCoverage',
              parameters: {
                transparent: false,
                format: 'image/jpeg',
              },
              credit: 'PNOA - IGN España',
            });
            const pnoaLayer = viewer.imageryLayers.addImageryProvider(pnoaProvider, 1); // Above OSM
            pnoaLayerRef.current = pnoaLayer;
          } catch (pnoaError) {
            logger.warn('[CesiumMap] Could not add PNOA layer:', pnoaError);
          }

          // Add Esri World Imagery (Global Satellite)
          try {
            Cesium.ArcGisMapServerImageryProvider.fromUrl(
              'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer', {
              enablePickFeatures: false
            }
            ).then((esriProvider: any) => {
              if (viewer.isDestroyed()) return;
              // Add above OSM (index 1) so it renders on top when visible.
              const esriLayer = viewer.imageryLayers.addImageryProvider(esriProvider, 1);
              esriLayerRef.current = esriLayer;
              esriLayer.show = baseLayerRef.current === 'esri';
              viewer.scene.requestRender?.();
            }).catch((esriError: any) => {
              logger.warn('[CesiumMap] Could not fetch Esri layer metadata:', esriError);
            });
          } catch (esriInitError) {
            logger.warn('[CesiumMap] Could not initialize Esri provider:', esriInitError);
          }

          // Add Cesium Ion default imagery (if token is available)
          try {
            if (import.meta.env.VITE_CESIUM_ION_TOKEN) {
              Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN;
              // Bing Maps Aerial (Asset ID 2)
              Cesium.IonImageryProvider.fromAssetId(2)
                .then((provider: any) => {
                  if (viewer.isDestroyed()) return;
                  // Add above OSM (index 1) so it renders on top when visible.
                  const cesiumLayer = viewer.imageryLayers.addImageryProvider(provider, 1);
                  cesiumLayerRef.current = cesiumLayer;
                  cesiumLayer.show = baseLayerRef.current === 'cesium';
                  viewer.scene.requestRender?.();
                })
                .catch((e: Error) => logger.warn('[CesiumMap] Error loading Ion Imagery', e));
            }
          } catch (cesiumError) {
            logger.warn('[CesiumMap] Could not set up Cesium Ion:', cesiumError);
          }

          // Apply initial visibility. Only the selected base layer is shown.
          if (osmLayer) osmLayer.show = baseLayer === 'osm'
          if (pnoaLayerRef.current) pnoaLayerRef.current.show = baseLayer === 'pnoa';
          if (esriLayerRef.current) esriLayerRef.current.show = baseLayer === 'esri';
          viewer.scene.requestRender?.();
        } catch (error) {
          logger.error('[CesiumMap] Error configuring initial imagery provider:', error);
        }

        viewerRef.current = viewer;
        setIsViewerReady(true); // Signal that viewer is ready
        // Expose viewer to context for map-layer components (only if ViewerProvider is available)
        if (setCesiumViewer) {
          setCesiumViewer(viewer);
        }

        // Set initial camera position based on available parcels
        (async () => {
          try {
            const parcels = await parcelApi.getParcels();
            const centroids: Array<[number, number]> = parcels
              .map(p => parcelCentroid(p))
              .filter((c): c is [number, number] => c !== null);
            applyInitialFraming(viewer, centroids);
          } catch {
            applyInitialFraming(viewer, []);
          }
        })();

      } catch (error) {
        logger.error('[CesiumMap] Error creating viewer:', error);
        // If viewer creation fails (likely WebGL), show the fallback
        setWebglFailed(true);
        return;
      }

      // Terrain, 3D Tiles, and WMS logic moved to separate useEffects

    } catch (error) {
      logger.error('[CesiumMap] Critical error during initialization:', error);
      viewerRef.current = null;
      setWebglFailed(true);
    }

    // Cleanup
    return () => {
      logger.debug('[CesiumMap] Cleanup function called. ViewerRef:', !!viewerRef.current);
      if (viewerRef.current) {
        try {
          logger.debug('[CesiumMap] Destroying viewer');
          logger.trace('[CesiumMap] Destroy stack trace');
          viewerRef.current.destroy();
        } catch (error) {
          logger.error('[CesiumMap] Error destroying viewer:', error);
        }
        viewerRef.current = null;
        setIsViewerReady(false);
        // Clear viewer from context (only if ViewerProvider is available)
        if (setCesiumViewer) {
          setCesiumViewer(null);
        }
      }
    };
  }, []); // Initialize only once

  // Log mount/unmount
  useEffect(() => {
    logger.debug('[CesiumMap] Component Mounted');
    return () => logger.debug('[CesiumMap] Component Unmounted');
  }, []);

  // DataHub uPlot → Cesium clock (imperative; detail.timestamp is Unix ms)
  useEffect(() => {
    if (!isViewerReady || !viewerRef.current) return;
    const viewer = viewerRef.current;
    const Cesium = window.Cesium;
    if (!Cesium) return;

    const onDataHubTimeHover = (e: Event) => {
      const ce = e as CustomEvent<{ timestamp?: number }>;
      const ts = ce.detail?.timestamp;
      if (typeof ts !== 'number' || !Number.isFinite(ts)) return;
      if (viewer.isDestroyed?.()) return;
      viewer.clock.shouldAnimate = false;
      viewer.clock.currentTime = Cesium.JulianDate.fromDate(new Date(ts));
    };

    window.addEventListener(DATAHUB_EVENT_TIME_HOVER, onDataHubTimeHover);
    return () => {
      window.removeEventListener(DATAHUB_EVENT_TIME_HOVER, onDataHubTimeHover);
    };
  }, [isViewerReady]);

  // Log to confirm refactor is active
  useEffect(() => {
    logger.debug('[CesiumMap] Viewer initialized (Refactored v3 - isViewerReady)');
  }, []);

  // Handle Terrain Updates (extracted hook)
  useTerrainProvider(viewerRef, enable3DTerrain, currentTerrainProvider, parcels, currentRegion, layerAutoMode);

  // Handle Base Layer Updates.
  // Only the selected base layer is shown; OSM appears only when explicitly picked.
  useEffect(() => {
    if (!isViewerReady) return;

    if (osmLayerRef.current) {
      osmLayerRef.current.show = baseLayer === 'osm';
    }

    if (pnoaLayerRef.current) {
      pnoaLayerRef.current.show = baseLayer === 'pnoa';
    }

    if (esriLayerRef.current) {
      esriLayerRef.current.show = baseLayer === 'esri';
    }

    if (cesiumLayerRef.current) {
      cesiumLayerRef.current.show = baseLayer === 'cesium';
    }

    viewerRef.current?.scene?.requestRender?.();
  }, [baseLayer, isViewerReady]);

  // Handle 3D Tiles Updates (extracted hook)
  use3DTiles(viewerRef, enable3DTiles, tilesetUrl);

  // NOTE: Module-specific Cesium layers (NDVI, vegetation indices) must use the slot system (map-layer slot)
  // External modules should expose their layers via the 'map-layer' slot
  // This hardcoded vegetation layer integration has been removed to maintain core independence
  // Modules must implement their own layer integration through the slot system

  // Duplicate preview logic removed. Moved to optimized block at bottom of file.

  // Handle Map Clicks (Picker Mode)
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || mode !== 'picker' || !onMapClick) return;

    // @ts-ignore
    const Cesium = window.Cesium;
    if (!Cesium) return;

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction((click: any) => {
      // Improved picking logic: try surface first (terrain/tiles), then ellipsoid
      let cartesian = viewer.scene.pickPosition(click.position);

      if (!cartesian) {
        // Fallback to ellipsoid picking (e.g. clicking on base globe without terrain)
        cartesian = viewer.camera.pickEllipsoid(click.position, viewer.scene.globe.ellipsoid);
      }

      if (cartesian) {
        // ... existing picker logic ...
        const cartographic = Cesium.Cartographic.fromCartesian(cartesian);
        const lon = Cesium.Math.toDegrees(cartographic.longitude);
        const lat = Cesium.Math.toDegrees(cartographic.latitude);


        // Add marker for visual feedback
        const markerId = 'picker-marker';
        viewer.entities.removeById(markerId);

        viewer.entities.add({
          id: markerId,
          position: cartesian,
          point: {
            pixelSize: 15,
            color: Cesium.Color.YELLOW,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          },
          label: {
            text: `${lat.toFixed(6)}, ${lon.toFixed(6)}`,
            font: '14pt sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            pixelOffset: new Cesium.Cartesian2(0, -20),
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
          },
        });

        onMapClick(lat, lon);
      }
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

    return () => {
      if (!viewer.isDestroyed()) {
        handler.destroy();
      }
    };
  }, [mode, onMapClick]);

  // Handle Map Clicks (View Mode - Entity Selection) (extracted hook)
  useEntitySelection(viewerRef, mode, onEntitySelect);

  useEffect(() => {
    const handleFullscreenChange = () => {
      const element = document.fullscreenElement;
      const active = element === wrapperRef.current;
      setIsFullscreen(active);
      if (viewerRef.current?.resize) {
        viewerRef.current.resize();
      } else {
        viewerRef.current?.scene?.requestRender?.();
      }
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    window.addEventListener('resize', handleFullscreenChange);
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      window.removeEventListener('resize', handleFullscreenChange);
    };
  }, []);

  const toggleFullscreen = () => {
    const element = wrapperRef.current;
    if (!element) return;
    if (document.fullscreenElement === element) {
      document.exitFullscreen?.().catch((err) => logger.warn('[CesiumMap] exitFullscreen failed', err));
    } else {
      element.requestFullscreen?.().catch((err) => logger.warn('[CesiumMap] requestFullscreen failed', err));
    }
  };

  // Height reference ref — updated when Cesium becomes available or terrain toggles
  const heightRef = useRef<any>(undefined);

  useEffect(() => {
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    heightRef.current = enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE;
  }, [enable3DTerrain, isViewerReady]);

  // ── Per-category entity effects ──────────────────────────────────
  // Each manages only its own entities via entityRefs, eliminating the
  // monolithic full-scene rebuild (removeAll) that caused visible flicker.

  // Robots effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.robot.forEach(e => viewer.entities.remove(e));
    entityRefs.current.robot.clear();
    viewer.entities.suspendEvents();

    robots.forEach((robot) => {
      try {
        const coordinates = getEntityCoordinates(robot);
        if (!coordinates) return;
        const [lon, lat] = coordinates;

        const robotName = typeof robot.name === 'string' ? robot.name : robot.name.value;
        const robotStatus = typeof robot.status === 'string' ? robot.status : robot.status?.value;
        const robotRisk = riskOverlay?.get(robot.id);
        const robotPointColor = robotRisk
          ? Cesium.Color.fromCssColorString(RISK_SEVERITY_COLORS[robotRisk.severity])
          : getRobotColor(Cesium, robotStatus);

        const entity = viewer.entities.add({
          id: `robot-${robot.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: robotName,
          point: {
            pixelSize: 15,
            color: robotPointColor,
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 2,
            heightReference: hr,
          },
          label: {
            text: robotName,
            font: '14px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -40),
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            heightReference: hr,
          },
        });
        entityRefs.current.robot.set(robot.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding robot:', robot, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, robots, riskOverlay, enable3DTerrain]);

  // Sensors effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.sensor.forEach(e => viewer.entities.remove(e));
    entityRefs.current.sensor.clear();
    viewer.entities.suspendEvents();

    logger.debug('[CesiumMap] Processing sensors:', sensors.length);
    sensors.forEach((sensor) => {
      try {
        const coordinates = getEntityCoordinates(sensor);
        logger.debug('[CesiumMap] Sensor', sensor.id, 'coordinates:', coordinates, 'location:', sensor.location);
        if (!coordinates) return;
        const [lon, lat] = coordinates;

        // Handle NGSI-LD namespaced name property
        let sensorName = 'Unknown Sensor';
        const nameProp = sensor.name;
        if (typeof nameProp === 'string') {
          sensorName = nameProp;
        } else if (nameProp && typeof nameProp === 'object' && 'value' in nameProp && typeof (nameProp as { value: string }).value === 'string') {
          sensorName = (nameProp as { value: string }).value;
        } else {
          const namespacedName = sensor['https://smartdatamodels.org/name'];
          if (namespacedName && typeof namespacedName === 'object' && 'value' in namespacedName && typeof (namespacedName as { value: string }).value === 'string') {
            sensorName = (namespacedName as { value: string }).value;
          } else if (typeof namespacedName === 'string') {
            sensorName = namespacedName;
          }
        }

        const modelUrl = getEntityModelUrl(sensor);
        const iconUrl = resolveEntityIconUrl(sensor, '/assets/icons/sensor-default.png');
        const sensorRisk = riskOverlay?.get(sensor.id);
        const sensorPointColor = sensorRisk
          ? Cesium.Color.fromCssColorString(RISK_SEVERITY_COLORS[sensorRisk.severity])
          : Cesium.Color.CYAN;

        const entityOptions: any = {
          id: `sensor-${sensor.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: sensorName,
          label: {
            text: sensorName,
            font: '12px sans-serif',
            fillColor: Cesium.Color.CYAN,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -30),
            heightReference: hr,
          },
        };

        if (modelUrl) {
          const fixedModelUrl = normalizeAssetUrl(modelUrl);
          entityOptions.model = {
            uri: fixedModelUrl,
            minimumPixelSize: 64,
            maximumScale: 20000,
            scale: (sensor.modelScale?.value || 1.0),
            heightReference: hr,
          };

          if (sensor.modelRotation?.value) {
            const [rX, rY, rZ] = sensor.modelRotation.value;
            const hpr = new Cesium.HeadingPitchRoll(
              Cesium.Math.toRadians(rZ),
              Cesium.Math.toRadians(rX),
              Cesium.Math.toRadians(rY)
            );
            const orientation = Cesium.Transforms.headingPitchRollQuaternion(
              Cesium.Cartesian3.fromDegrees(lon, lat, 0),
              hpr
            );
            entityOptions.orientation = orientation;
          }
        } else if (iconUrl && iconUrl !== '/assets/icons/sensor-default.png') {
          let processedIconUrl = iconUrl;
          if (iconUrl.startsWith('/') && !iconUrl.startsWith('//')) {
            processedIconUrl = `${window.location.origin}${iconUrl}`;
          }

          logger.debug(`[CesiumMapDebug] Sensor ${sensor.id} using icon: ${processedIconUrl} (Original: ${iconUrl})`);

          const validIcon = processedIconUrl.startsWith('http') || processedIconUrl.startsWith('data:');

          if (validIcon) {
            const fixedIconUrl = normalizeAssetUrl(processedIconUrl);
            entityOptions.billboard = {
              image: fixedIconUrl,
              width: 32,
              height: 32,
              heightReference: hr,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            };
          } else {
            entityOptions.point = {
              pixelSize: 10,
              color: sensorPointColor,
              outlineColor: Cesium.Color.WHITE,
              outlineWidth: 1,
              heightReference: hr,
            };
          }
        } else {
          entityOptions.point = {
            pixelSize: 10,
            color: sensorPointColor,
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1,
            heightReference: hr,
          };
        }

        const entity = viewer.entities.add(entityOptions);
        entityRefs.current.sensor.set(sensor.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding sensor:', sensor, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, sensors, riskOverlay, enable3DTerrain]);

  // Field photos effect — camera billboards with capture date label
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.fieldPhoto.forEach(e => viewer.entities.remove(e));
    entityRefs.current.fieldPhoto.clear();
    viewer.entities.suspendEvents();

    const cameraIcon = getIconDataUri('icon:camera');
    fieldPhotos.forEach((photo) => {
      try {
        if (photo.lng == null || photo.lat == null) return;
        const label = photo.dateObserved
          ? new Date(photo.dateObserved).toLocaleDateString()
          : '';
        const entity = viewer.entities.add({
          id: `fieldphoto-${photo.id}`,
          position: Cesium.Cartesian3.fromDegrees(photo.lng, photo.lat, 0),
          billboard: {
            image: cameraIcon,
            width: 32,
            height: 32,
            heightReference: hr,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          },
          label: {
            text: label,
            font: '11px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -34),
            heightReference: hr,
          },
        });
        entityRefs.current.fieldPhoto.set(photo.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding field photo:', photo, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, fieldPhotos, enable3DTerrain]);

  // Machines effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.machine.forEach(e => viewer.entities.remove(e));
    entityRefs.current.machine.clear();
    viewer.entities.suspendEvents();

    machines.forEach((machine) => {
      try {
        const coordinates = getEntityCoordinates(machine);
        if (!coordinates) return;
        const [lon, lat] = coordinates;

        const machineName = typeof machine.name === 'string' ? machine.name : machine.name.value;
        const operationType = typeof machine.operationType === 'string'
          ? machine.operationType
          : machine.operationType?.value;

        const entityOptions: any = {
          id: `machine-${machine.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: machineName,
          point: {
            pixelSize: 18,
            color: getMachineColor(Cesium, operationType),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 2,
            heightReference: hr,
          },
          label: {
            text: machineName,
            font: '14px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -40),
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            heightReference: hr,
          },
        };

        const modelUrl = getEntityModelUrl(machine, '/icons/machines/tractor.glb');
        if (modelUrl) {
          entityOptions.model = {
            uri: normalizeAssetUrl(modelUrl),
            minimumPixelSize: 64,
            maximumScale: 20000,
            heightReference: hr,
          };
        }

        if (!modelUrl) {
          const iconUrl = resolveEntityIconUrl(machine, '/icons/machines/tractor.png');
          if (iconUrl) {
            entityOptions.billboard = {
              image: normalizeAssetUrl(iconUrl),
              width: 48,
              height: 48,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            };
            delete entityOptions.point;
          }
        }

        const entity = viewer.entities.add(entityOptions);
        entityRefs.current.machine.set(machine.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding machine:', machine, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, machines, enable3DTerrain]);

  // Livestock effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.livestock.forEach(e => viewer.entities.remove(e));
    entityRefs.current.livestock.clear();
    viewer.entities.suspendEvents();

    livestock.forEach((animal) => {
      try {
        const coordinates = getEntityCoordinates(animal);
        if (!coordinates) return;
        const [lon, lat] = coordinates;

        const animalName = typeof animal.name === 'string' ? animal.name : animal.name.value;
        const activity = typeof animal.activity === 'string' ? animal.activity : animal.activity?.value;
        const species = typeof animal.species === 'string' ? animal.species : animal.species?.value;

        const entityOptions: any = {
          id: `livestock-${animal.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: animalName,
          point: {
            pixelSize: 12,
            color: getLivestockColor(Cesium, activity),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1,
            heightReference: hr,
          },
          label: {
            text: animalName,
            font: '12px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -30),
            heightReference: hr,
          },
        };

        const modelUrl = getEntityModelUrl(animal, species === 'Bos taurus' ? '/icons/livestock/cow.glb' : '/icons/livestock/animal.glb');
        if (modelUrl) {
          entityOptions.model = {
            uri: normalizeAssetUrl(modelUrl),
            minimumPixelSize: 32,
            maximumScale: 20000,
            heightReference: hr,
          };
        }

        if (!modelUrl) {
          const iconUrl = resolveEntityIconUrl(animal, species === 'Bos taurus' ? '/icons/livestock/cow.png' : '/icons/livestock/animal.png');
          if (iconUrl) {
            entityOptions.billboard = {
              image: normalizeAssetUrl(iconUrl),
              width: 32,
              height: 32,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            };
            delete entityOptions.point;
          }
        }

        const entity = viewer.entities.add(entityOptions);
        entityRefs.current.livestock.set(animal.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding livestock:', animal, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, livestock, enable3DTerrain]);

  // Weather stations effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.weather.forEach(e => viewer.entities.remove(e));
    entityRefs.current.weather.clear();
    viewer.entities.suspendEvents();

    weatherStations.forEach((station) => {
      try {
        const coordinates = getEntityCoordinates(station);
        if (!coordinates) return;
        const [lon, lat] = coordinates;

        const stationName = typeof station.name === 'string' ? station.name : station.name.value;

        const entityOptions: any = {
          id: `weather-${station.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: stationName,
          point: {
            pixelSize: 14,
            color: Cesium.Color.SKYBLUE,
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 2,
            heightReference: hr,
          },
          label: {
            text: stationName,
            font: '12px sans-serif',
            fillColor: Cesium.Color.SKYBLUE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -30),
            heightReference: hr,
          },
        };

        const modelUrl = getEntityModelUrl(station, '/icons/weather/station.glb');
        if (modelUrl) {
          entityOptions.model = {
            uri: normalizeAssetUrl(modelUrl),
            minimumPixelSize: 48,
            maximumScale: 20000,
            heightReference: hr,
          };
        }

        if (!modelUrl) {
          const iconUrl = resolveEntityIconUrl(station, '/icons/weather/station.png');
          if (iconUrl) {
            entityOptions.billboard = {
              image: normalizeAssetUrl(iconUrl),
              width: 40,
              height: 40,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            };
            delete entityOptions.point;
          }
        }

        const entity = viewer.entities.add(entityOptions);
        entityRefs.current.weather.set(station.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding weather station:', station, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, weatherStations, enable3DTerrain]);

  // Crops (AgriCrop) effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.crop.forEach(e => viewer.entities.remove(e));
    entityRefs.current.crop.clear();
    viewer.entities.suspendEvents();

    crops.forEach((crop) => {
      try {
        const cropName = typeof crop.name === 'string' ? crop.name : crop.name?.value || 'Unknown Crop';
        const cropType = typeof crop.agroVocConcept === 'string' ? crop.agroVocConcept : crop.agroVocConcept?.value;

        const geomType = getEntityGeometryType(crop);
        const coordinates = getEntityCoordinates(crop);

        if (!coordinates) return;

        if (geomType === 'Polygon') {
          const coords = coordinates[0];
          const hierarchy = (coords as any[]).map((c: any) => Cesium.Cartesian3.fromDegrees(c[0], c[1]));

          const entity = viewer.entities.add({
            id: `crop-${crop.id}`,
            name: cropName,
            polygon: {
              hierarchy: hierarchy,
              material: Cesium.Color.GREEN.withAlpha(0.4),
              outline: true,
              outlineColor: Cesium.Color.DARKGREEN,
              heightReference: hr,
            },
            description: `Type: ${cropType}`
          });
          entityRefs.current.crop.set(crop.id, entity);
        } else {
          const [lon, lat] = coordinates as [number, number];
          const entity = viewer.entities.add({
            id: `crop-${crop.id}`,
            position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
            name: cropName,
            point: {
              pixelSize: 10,
              color: Cesium.Color.GREEN,
              outlineColor: Cesium.Color.WHITE,
              outlineWidth: 1,
              heightReference: hr,
            },
            label: {
              text: cropName,
              font: '12px sans-serif',
              fillColor: Cesium.Color.GREEN,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              pixelOffset: new Cesium.Cartesian2(0, -20),
              heightReference: hr,
            }
          });
          entityRefs.current.crop.set(crop.id, entity);
        }
      } catch (e) {
        logger.warn('[CesiumMap] Error adding crop:', crop, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, crops, enable3DTerrain]);

  // Buildings (AgriBuilding) effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.building.forEach(e => viewer.entities.remove(e));
    entityRefs.current.building.clear();
    viewer.entities.suspendEvents();

    buildings.forEach((building) => {
      try {
        const coordinates = getEntityCoordinates(building);
        if (!coordinates) return;

        const buildingName = typeof building.name === 'string' ? building.name : building.name?.value || 'Unknown Building';
        const category = typeof building.category === 'string' ? building.category : building.category?.value;
        const geomType = getEntityGeometryType(building);

        if (geomType === 'Polygon') {
          const coords = coordinates[0];
          const hierarchy = (coords as any[]).map((c: any) => Cesium.Cartesian3.fromDegrees(c[0], c[1]));

          const entity = viewer.entities.add({
            id: `building-${building.id}`,
            name: buildingName,
            polygon: {
              hierarchy: hierarchy,
              material: Cesium.Color.GRAY.withAlpha(0.9),
              extrudedHeight: 5,
              outline: true,
              outlineColor: Cesium.Color.BLACK,
              heightReference: hr,
            },
            description: `Category: ${category}`
          });
          entityRefs.current.building.set(building.id, entity);
        } else {
          const [lon, lat] = coordinates as [number, number];

          const entity = viewer.entities.add({
            id: `building-${building.id}`,
            position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
            name: buildingName,
            model: {
              uri: '/icons/infrastructure/building.glb',
              minimumPixelSize: 64,
              heightReference: hr,
            },
            label: {
              text: buildingName,
              font: '12px sans-serif',
              fillColor: Cesium.Color.WHITE,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              pixelOffset: new Cesium.Cartesian2(0, -40),
              heightReference: hr,
            }
          });
          entityRefs.current.building.set(building.id, entity);
        }
      } catch (e) {
        logger.warn('[CesiumMap] Error adding building:', building, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, buildings, enable3DTerrain]);

  // Devices effect
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.device.forEach(e => viewer.entities.remove(e));
    entityRefs.current.device.clear();
    viewer.entities.suspendEvents();

    devices.forEach((device) => {
      try {
        const coordinates = getEntityCoordinates(device);
        if (!coordinates) return;

        const [lon, lat] = coordinates;
        const deviceName = typeof device.name === 'string' ? device.name : device.name?.value || 'Unknown Device';
        const category = typeof device.category === 'string' ? device.category : device.category?.value;

        const entity = viewer.entities.add({
          id: `device-${device.id}`,
          position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
          name: deviceName,
          point: {
            pixelSize: 8,
            color: Cesium.Color.ORANGE,
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 1,
            heightReference: hr,
          },
          label: {
            text: deviceName,
            font: '10px sans-serif',
            fillColor: Cesium.Color.ORANGE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            pixelOffset: new Cesium.Cartesian2(0, -20),
            heightReference: hr,
          },
          description: `Category: ${category}`
        });
        entityRefs.current.device.set(device.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding device:', device, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, devices, enable3DTerrain]);

  // Trees effect (OliveTree, AgriTree, FruitTree, Vine) — with 3D model support
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.tree.forEach(e => viewer.entities.remove(e));
    entityRefs.current.tree.clear();
    viewer.entities.suspendEvents();

    if (trees.length > 0) {
      logger.debug('[CesiumMap] Rendering trees:', trees.length);
    }
    trees.forEach((tree) => {
      try {
        const coordinates = getEntityCoordinates(tree);
        if (!coordinates) {
          logger.warn('[CesiumMap] Tree without coordinates:', tree.id);
          return;
        }

        const [lon, lat] = coordinates;
        const treeName = typeof tree.name === 'string' ? tree.name : tree.name?.value || 'Unknown Tree';
        const treeType = tree.type || 'AgriTree';
        const modelUrl = getEntityModelUrl(tree, undefined);
        const modelScale = tree.modelScale?.value || tree.modelScale || 1;

        if (modelUrl) {
          logger.debug('[CesiumMap] Adding tree with model:', tree.id, modelUrl);

          const modelRotation = tree.modelRotation?.value || tree.modelRotation || [0, 0, 0];
          const heading = Cesium.Math.toRadians(modelRotation[0] || 0);
          const pitch = Cesium.Math.toRadians(modelRotation[1] || 0);
          const roll = Cesium.Math.toRadians(modelRotation[2] || 0);

          const position = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
          const hpr = new Cesium.HeadingPitchRoll(heading, pitch, roll);
          const orientation = Cesium.Transforms.headingPitchRollQuaternion(position, hpr);

          const entity = viewer.entities.add({
            id: `tree-${tree.id}`,
            position: position,
            orientation: orientation,
            name: treeName,
            model: {
              uri: normalizeAssetUrl(modelUrl),
              scale: modelScale,
              minimumPixelSize: 32,
              maximumScale: 20000,
              heightReference: hr,
            },
            label: {
              text: treeName,
              font: '12px sans-serif',
              fillColor: Cesium.Color.GREEN,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              pixelOffset: new Cesium.Cartesian2(0, -40),
              heightReference: hr,
              show: false,
            },
            description: `Type: ${treeType}`,
          });
          entityRefs.current.tree.set(tree.id, entity);
        } else {
          const entity = viewer.entities.add({
            id: `tree-${tree.id}`,
            position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
            name: treeName,
            billboard: {
              image: '/icons/trees/olive-tree.svg',
              width: 24,
              height: 24,
              heightReference: hr,
            },
            label: {
              text: treeName,
              font: '11px sans-serif',
              fillColor: Cesium.Color.FORESTGREEN,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              pixelOffset: new Cesium.Cartesian2(0, -20),
              heightReference: hr,
            },
            description: `Type: ${treeType}`,
          });
          entityRefs.current.tree.set(tree.id, entity);
        }
      } catch (e) {
        logger.warn('[CesiumMap] Error adding tree:', tree, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, trees, enable3DTerrain]);

  // Energy trackers (AgriEnergyTracker) effect — MultiPoint -> individual models
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    const hr = heightRef.current || (enable3DTerrain
      ? Cesium.HeightReference.CLAMP_TO_GROUND
      : Cesium.HeightReference.NONE);

    entityRefs.current.tracker.forEach(e => viewer.entities.remove(e));
    entityRefs.current.tracker.clear();
    viewer.entities.suspendEvents();

    energyTrackers.forEach((tracker) => {
      try {
        const trackerName = typeof tracker.name === 'string' ? tracker.name : tracker.name?.value || 'Solar Tracker';
        const modelUrl = getEntityModelUrl(tracker);
        const modelScale = tracker.modelScale?.value ?? tracker.modelScale ?? 1;
        const tilt = tracker.tilt?.value ?? 0;
        const azimuth = tracker.azimuth?.value ?? 0;
        const modelRotation = tracker.modelRotation?.value || [azimuth, -tilt, 0];

        // Extract all coordinates — supports both Point and MultiPoint
        const location = tracker.location?.value || tracker.location;
        let coordsList: [number, number][] = [];

        if (location?.type === 'MultiPoint' && Array.isArray(location.coordinates)) {
          coordsList = location.coordinates;
        } else if (location?.type === 'Point' && Array.isArray(location.coordinates)) {
          coordsList = [location.coordinates];
        } else {
          // Fallback: try standard extraction
          const coords = getEntityCoordinates(tracker);
          if (coords) coordsList = [[coords[0], coords[1]]];
        }

        if (coordsList.length === 0) return;

        coordsList.forEach((coord, idx) => {
          const [lon, lat] = coord;
          const entityId = `tracker-${tracker.id}-${idx}`;

          if (modelUrl) {
            const heading = Cesium.Math.toRadians(modelRotation[0] || 0);
            const pitch = Cesium.Math.toRadians(modelRotation[1] || 0);
            const roll = Cesium.Math.toRadians(modelRotation[2] || 0);

            const position = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
            const hpr = new Cesium.HeadingPitchRoll(heading, pitch, roll);
            const orientation = Cesium.Transforms.headingPitchRollQuaternion(position, hpr);

            const entity = viewer.entities.add({
              id: entityId,
              position,
              orientation,
              name: `${trackerName} [${idx + 1}]`,
              model: {
                uri: normalizeAssetUrl(modelUrl),
                scale: modelScale,
                minimumPixelSize: 32,
                maximumScale: 20000,
                heightReference: hr,
              },
              label: {
                text: idx === 0 ? trackerName : undefined,
                font: '12px sans-serif',
                fillColor: Cesium.Color.YELLOW,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                pixelOffset: new Cesium.Cartesian2(0, -40),
                heightReference: hr,
                show: idx === 0,
              },
              description: ['Type: AgriEnergyTracker', 'Tilt: ' + tilt + '°', 'Azimuth: ' + azimuth + '°', 'Instance: ' + (idx + 1) + '/' + coordsList.length].join('\n'),
            });
            entityRefs.current.tracker.set(`${tracker.id}-${idx}`, entity);
          } else {
            // Fallback: yellow point marker
            const entity = viewer.entities.add({
              id: entityId,
              position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
              name: `${trackerName} [${idx + 1}]`,
              point: {
                pixelSize: 10,
                color: Cesium.Color.YELLOW.withAlpha(0.9),
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 2,
                heightReference: hr,
              },
              label: idx === 0 ? {
                text: trackerName,
                font: '12px sans-serif',
                fillColor: Cesium.Color.YELLOW,
                outlineColor: Cesium.Color.BLACK,
                outlineWidth: 2,
                pixelOffset: new Cesium.Cartesian2(0, -20),
                heightReference: hr,
              } : undefined,
            });
            entityRefs.current.tracker.set(`${tracker.id}-${idx}`, entity);
          }
        });
      } catch (e) {
        logger.warn('[CesiumMap] Error adding energy tracker:', tracker.id, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, energyTrackers, enable3DTerrain]);

  // Parcels effect (border-only + optional fill on risk/selection)
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || !isViewerReady) return;
    const Cesium = (window as any).Cesium;
    if (!Cesium) return;
    entityRefs.current.parcel.forEach(e => viewer.entities.remove(e));
    entityRefs.current.parcel.clear();
    viewer.entities.suspendEvents();

    const isParcelSelected = (parcelId: string) => selectedEntity?.id === parcelId;

    parcels.forEach((parcel) => {
      try {
        const coordinates = getEntityCoordinates(parcel);
        if (!coordinates) return;
        const type = getEntityGeometryType(parcel);

        const positions: any[] = [];
        if (type === 'Polygon' && Array.isArray(coordinates[0])) {
          coordinates[0].forEach((coord: any) => {
            if (Array.isArray(coord) && coord.length >= 2) {
              const lon = Number(coord[0]);
              const lat = Number(coord[1]);
              if (!isNaN(lon) && !isNaN(lat)) {
                positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, 0));
              }
            }
          });
        }
        if (positions.length < 3) {
          logger.warn(`[CesiumMap] Skipping parcel ${parcel.id}: Invalid geometry (less than 3 points)`);
          return;
        }

        const parcelName = parcel.name || parcel.id;
        const isSelected = isParcelSelected(parcel.id);
        const riskInfo = riskOverlay?.get(parcel.id);

        // Defaults: green border, no fill
        let borderColor: any = Cesium.Color.fromCssColorString('#4ade80');
        let borderWidth = 3;
        // Near-zero alpha keeps the polygon pickable by Cesium's GPU pick pass
        // (TRANSPARENT/alpha=0 is silently excluded from the pick framebuffer).
        let fillColor: any = Cesium.Color.WHITE.withAlpha(0.004);

        // Risk takes precedence over default
        if (riskInfo) {
          const riskCss = RISK_SEVERITY_COLORS[riskInfo.severity];
          borderColor = Cesium.Color.fromCssColorString(riskCss);
          fillColor = Cesium.Color.fromCssColorString(riskCss).withAlpha(0.25);
        }

        // Selection wins (cyan border + faint cyan fill as a "find me" hint)
        if (isSelected) {
          borderColor = Cesium.Color.fromCssColorString('#06b6d4');
          borderWidth = 4;
          fillColor = Cesium.Color.CYAN.withAlpha(0.06);
        }

        // Closed ring for the polyline
        const ringPositions = [...positions, positions[0]];

        const entity = viewer.entities.add({
          id: `parcel-${parcel.id}`,
          name: parcelName,
          polygon: {
            hierarchy: positions,
            material: fillColor,
            classificationType: enable3DTiles
              ? Cesium.ClassificationType.BOTH
              : Cesium.ClassificationType.TERRAIN,
            arcType: Cesium.ArcType.GEODESIC,
          },
          polyline: {
            positions: ringPositions,
            width: borderWidth,
            material: new Cesium.PolylineOutlineMaterialProperty({
              color: borderColor,
              outlineColor: Cesium.Color.BLACK.withAlpha(0.6),
              outlineWidth: 1,
            }),
            clampToGround: true,
            arcType: Cesium.ArcType.GEODESIC,
          },
          label: {
            text: parcelName,
            font: isSelected ? '14px sans-serif' : '12px sans-serif',
            fillColor: Cesium.Color.WHITE,
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: Cesium.VerticalOrigin.CENTER,
            horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 5000),
            show: true,
          },
          description: [
            '<div class="p-2">',
            '<h3 class="font-bold">' + parcelName + '</h3>',
            '<p>Área: ' + (parcel.area || 'N/A') + ' ha</p>',
            '<p>Cultivo: ' + (parcel.cropType || '—') + '</p>',
            '</div>'
          ].join('\n')
        });
        entityRefs.current.parcel.set(parcel.id, entity);
      } catch (e) {
        logger.warn('[CesiumMap] Error adding parcel:', parcel.id, e);
      }
    });

    viewer.entities.resumeEvents();
    viewer.scene.requestRender();
  }, [isViewerReady, parcels, enable3DTerrain, enable3DTiles, selectedEntity?.id, riskOverlay]);

  useFlyToEntity(viewerRef, selectedEntity, viewerContext?.entityListCameraNonce ?? 0);

  // Handle 3D Model Preview (PREVIEW_MODEL mode) (extracted hook)
  useModelPreview(viewerRef, isViewerReady, viewerContext);

  // Show fallback if WebGL is not available
  if (webglFailed) {
    return (
      <div
        ref={wrapperRef}
        className="relative w-full h-full rounded-xl overflow-hidden shadow-lg border border-slate-700 bg-slate-900"
      >
        <WebGLFallback />
      </div>
    );
  }

  return (
    <div
      ref={wrapperRef}
      className={`relative w-full h-full rounded-xl overflow-hidden shadow-lg border border-slate-700 bg-slate-900 group ${isFullscreen ? 'fixed inset-0 z-50 rounded-none' : ''}`}
    >
      <div ref={containerRef} className="w-full h-full" />

      {/* Loading Overlay */}
      {!isViewerReady && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-900 z-10">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"></div>
            <p className="text-slate-400 text-sm font-medium">Cargando mapa 3D...</p>
          </div>
        </div>
      )}

      {/* Controls */}
      {showControls && isViewerReady && (
        <div className="absolute top-4 right-4 flex flex-col gap-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
          {/* Fullscreen Toggle */}
          <Button
            onClick={toggleFullscreen}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg shadow-lg transition-all border border-slate-600"
            title={isFullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'}
          >
            {isFullscreen ? <Minimize2 size={20} /> : <Maximize2 size={20} />}
          </Button>

          {/* 3D Terrain Toggle */}
          <Button
            type="button"
            onClick={() => {
              // Toggle logic would go here if we had a setEnable3DTerrain prop or state
              // For now, just a visual toggle if we can't change the prop from here
              logger.debug('Toggle 3D Terrain clicked');
            }}
            className={`p-2 rounded-lg shadow-lg transition-all border border-slate-600 ${enable3DTerrain ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-800 hover:bg-slate-700 text-slate-300'}`}
            title={enable3DTerrain ? 'Desactivar relieve 3D' : 'Activar relieve 3D'}
          >
            <Mountain size={20} />
          </Button>

          {/* Layer Picker (Base Map & Terrain) */}
          <div className="relative">
            <Button
              type="button"
              onClick={() => setShowTerrainPicker(!showTerrainPicker)}
              className="p-2 bg-slate-800 hover:bg-slate-700 text-white rounded-lg shadow-lg transition-all border border-slate-600"
              title="Seleccionar capas"
            >
              <Layers size={20} />
            </Button>

            {showTerrainPicker && (
              <div className="absolute right-full top-0 mr-2 bg-slate-800 rounded-lg shadow-xl border border-slate-600 min-w-[220px] overflow-hidden z-20">
                {/* Base Map Section */}
                <div className="px-3 py-2 bg-slate-900/50 border-b border-slate-700">
                  <p className="text-xs font-semibold text-slate-300">Mapa Base</p>
                </div>
                <div className="p-1 border-b border-slate-700">
                  {/* Auto toggle */}
                  <Button
                    onClick={() => {
                      manualPickRef.current = false;
                      setLayerAutoMode(true);
                      setShowTerrainPicker(false);
                    }}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${layerAutoMode ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                  >
                    <div className="font-medium">🌍 Auto</div>
                    <div className="text-xs text-slate-500">Automático según región</div>
                  </Button>
                  {/* Always-visible base layer options. Selecting one disables auto mode. */}
                  <Button
                    onClick={() => { manualPickRef.current = true; setLayerAutoMode(false); setBaseLayer('osm'); }}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${baseLayer === 'osm' && !layerAutoMode ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                  >
                    <div className="font-medium">Callejero (OSM)</div>
                    <div className="text-xs text-slate-500">OpenStreetMap global</div>
                  </Button>
                  <Button
                    onClick={() => { manualPickRef.current = true; setLayerAutoMode(false); setBaseLayer('pnoa'); }}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${baseLayer === 'pnoa' && !layerAutoMode ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                  >
                    <div className="font-medium">Ortofoto (PNOA)</div>
                    <div className="text-xs text-slate-500">Alta resolución (España)</div>
                  </Button>
                  <Button
                    onClick={() => { manualPickRef.current = true; setLayerAutoMode(false); setBaseLayer('esri'); }}
                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${baseLayer === 'esri' && !layerAutoMode ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                  >
                    <div className="font-medium">Satélite (Esri)</div>
                    <div className="text-xs text-slate-500">Imágenes satelitales globales</div>
                  </Button>
                  {import.meta.env.VITE_CESIUM_ION_TOKEN && (
                    <Button
                      onClick={() => { manualPickRef.current = true; setLayerAutoMode(false); setBaseLayer('cesium'); }}
                      className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${baseLayer === 'cesium' && !layerAutoMode ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                    >
                      <div className="font-medium">Satélite (Cesium Ion)</div>
                      <div className="text-xs text-slate-500">Bing Maps Aerial (Premium)</div>
                    </Button>
                  )}
                </div>

                {/* Terrain Section */}
                {enable3DTerrain && (
                  <>
                    <div className="px-3 py-2 bg-slate-900/50 border-b border-slate-700">
                      <p className="text-xs font-semibold text-slate-300">Modelo de Elevación</p>
                    </div>
                    <div className="p-1">
                      {[
                        { id: 'auto', name: 'Automático (Región)', desc: 'IDENA/IGN/EU según cámara' },
                        { id: 'idena', name: 'IDENA (Navarra)', desc: 'Alta precisión (MDT05)' },
                        { id: 'ign', name: 'IGN (España)', desc: 'Cobertura nacional (MDT25)' },
                        ...(import.meta.env.VITE_CESIUM_ION_TOKEN ? [
                          { id: 'cesium_world', name: 'Cesium World (Global)', desc: 'Terreno mundial (~30m)' }
                        ] : [])
                      ].map((provider) => (
                        <Button
                          key={provider.id}
                          onClick={() => {
                            if (provider.id !== 'auto') { manualPickRef.current = true; setLayerAutoMode(false); }
                            else { manualPickRef.current = false; setLayerAutoMode(true); }
                            setCurrentTerrainProvider(provider.id);
                            setShowTerrainPicker(false);
                          }}
                          className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${currentTerrainProvider === provider.id ? 'bg-blue-600/20 text-blue-400' : 'text-slate-300 hover:bg-slate-700'}`}
                        >
                          <div className="font-medium">{provider.name}</div>
                          <div className="text-xs text-slate-500">{provider.desc}</div>
                        </Button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Risk severity legend — shown when risk overlay is active */}
      {riskOverlay && riskOverlay.size > 0 && (
        <div className="absolute bottom-4 left-4 z-10 bg-slate-900 rounded-lg px-3 py-2 text-xs text-white border border-slate-600 pointer-events-none">
          <p className="font-semibold mb-1.5 text-slate-300">Riesgo</p>
          {(['critical', 'high', 'medium', 'low'] as const).map(sev => (
            <div key={sev} className="flex items-center gap-1.5 mb-0.5">
              <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: RISK_SEVERITY_COLORS[sev] }} />
              <span className="capitalize text-slate-200">{{ critical: 'Crítico', high: 'Alto', medium: 'Medio', low: 'Bajo' }[sev]}</span>
            </div>
          ))}
        </div>
      )}

      {/* Logic Components */}
      <CesiumStampRenderer />

      {/* Map Layer Slot - Legacy in-map rendering path.
          UnifiedViewer renders this slot externally; disable here there to avoid duplicates. */}
      {renderMapLayerSlot && viewerContext && isViewerReady && viewerRef.current && (
        <SlotRenderer
          slot="map-layer"
          inline
          additionalProps={{ viewer: viewerRef.current }}
        />
      )}

      {/* Search lupa — centered on screen, above all overlays */}
      {isViewerReady && viewerRef.current && (
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-50">
          <MapSearchLupa onPick={(r) => flyToForResult(viewerRef.current, r)} />
        </div>
      )}

      {/* Legend/Info Overlay could go here */}
    </div>
  );
});

export default CesiumMap;

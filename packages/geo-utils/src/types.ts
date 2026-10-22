/** Supported spectral index types */
export type SpectralIndex =
  | 'NDVI'
  | 'NDWI'
  | 'EVI'
  | 'SAVI'
  | 'NBR'
  | 'NDBI'
  | 'GNDVI'
  | 'OSAVI'
  | 'CIre';

/** Options for spectral index computation */
export interface SpectralOptions {
  index: SpectralIndex;
  /** Band number for RED (default: 4 for Sentinel-2) */
  redBand?: number;
  /** Band number for NIR (default: 8 for Sentinel-2) */
  nirBand?: number;
  /** Band number for BLUE (default: 2 for Sentinel-2, used in EVI) */
  blueBand?: number;
  /** Band number for SWIR (default: 12 for Sentinel-2, used in NDWI/NBR) */
  swirBand?: number;
}

/** Result of a spectral index computation */
export interface SpectralResult {
  values: Float64Array;
  width: number;
  height: number;
  stats: { min: number; max: number; mean: number; std: number };
  epsg: number;
}

/** A geolocated sensor reading point.
 * @param x - Longitude in EPSG:4326
 * @param y - Latitude in EPSG:4326
 * @param value - Numeric measurement (temperature in °C, humidity in %, NDVI 0-1, etc.) */
export interface SensorPoint {
  /** Longitude in EPSG:4326 */
  x: number;
  /** Latitude in EPSG:4326 */
  y: number;
  /** Numeric measurement value */
  value: number;
}

/** Interpolation method */
export type InterpolationMethod = 'idw' | 'linear';

/** Geographic bounding box: [minLon, minLat, maxLon, maxLat] in EPSG:4326 */
export type LatLngBounds = [number, number, number, number];

/** Colormap preset */
export type Colormap =
  | 'temperature'  // blue→red (custom Nekazari)
  | 'ndvi'         // brown→green (custom Nekazari)
  | 'stress'       // green→red (custom Nekazari)
  | 'viridis'      // geolibre native
  | 'magma'        // geolibre native
  | 'turbo'        // geolibre native
  | 'terrain'      // geolibre native
  | 'grayscale';   // geolibre native

/** Options for heatmap interpolation */
export interface HeatmapOptions {
  /** Cells per axis (default: 50 → 50×50 = 2500 cells) */
  resolution?: number;
  /** Interpolation method (default: 'idw') */
  method?: InterpolationMethod;
  /** IDW power parameter (default: 2) */
  power?: number;
  /** Output colormap (default: 'temperature') */
  colormap?: Colormap;
}

/** Result of heatmap interpolation */
export interface HeatmapResult {
  grid: number[][];
  /** [minLon, minLat, maxLon, maxLat] in EPSG:4326 */
  bounds: LatLngBounds;
  stats: { min: number; max: number; mean: number };
  /** PNG bytes rendered with the colormap */
  png: Uint8Array;
}

/** Options for PMTiles generation */
export interface PMTilesOptions {
  colormap: Colormap;
  minZoom?: number;
  maxZoom?: number;
}

/** Zonal statistics result */
export interface ZonalStatsResult {
  mean: number;
  min: number;
  max: number;
  sum: number;
  std: number;
  count: number;
  areaM2: number;
}

/** WASM load mode */
export type WasmMode = 'light' | 'full';

/** Options for WASM initialization */
export interface InitOptions {
  /** 'light' = only browser lib (4.3MB), 'full' = +CLI tools (17MB). Auto-upgrades on demand. */
  mode?: WasmMode;
  /** Custom URL for the WASM binary (V2: CDN) */
  wasmUrl?: string;
}

/** Custom error codes */
export type GeoErrorCode =
  | 'WASM_NOT_INITIALIZED'
  | 'WASM_LOAD_FAILED'
  | 'INVALID_COG'
  | 'INSUFFICIENT_POINTS'
  | 'INVALID_GEOMETRY'
  | 'TOOL_EXECUTION_FAILED'
  | 'UNSUPPORTED_INDEX';

export class GeoError extends Error {
  constructor(public code: GeoErrorCode, message: string) {
    super(`[${code}] ${message}`);
    this.name = 'GeoError';
  }
}

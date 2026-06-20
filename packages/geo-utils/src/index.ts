// WASM loader
export { initGeoLibre, getBrowserLib, runTool, isGeoLibreReady, listTools } from './wasm';

// Spectral indices
export { computeSpectralIndex } from './spectral';

// Spatial interpolation
export { interpolateHeatmap } from './interpolation';

// Raster utilities
export { rasterToPmtiles, renderRasterPNG, reprojectRaster } from './raster';

// Vector utilities
export { writeGeoParquet, readGeoParquet, reprojectVector, vectorConvert } from './vector';

// Zonal statistics
export { zonalStats } from './zonal';

// Types
export type {
  SpectralIndex, SpectralOptions, SpectralResult,
  SensorPoint, InterpolationMethod, LatLngBounds,
  Colormap, HeatmapOptions, HeatmapResult,
  PMTilesOptions, ZonalStatsResult,
  WasmMode, InitOptions, GeoErrorCode,
} from './types';

export { GeoError } from './types';

# @nekazari/geo-utils

Edge geo-processing utilities wrapping `geolibre-wasm` for Nekazari modules.

Provides spectral indices, spatial interpolation, PMTiles generation, GeoParquet I/O,
and zonal statistics — all computed in the browser via WebAssembly, or on the backend
via geolibre-wasm Python.

## Install

```bash
npm install @nekazari/geo-utils
# or
pnpm add @nekazari/geo-utils
```

## Usage

```typescript
import { initGeoLibre, computeSpectralIndex, interpolateHeatmap } from '@nekazari/geo-utils';

// Initialize WASM (lazy, only when needed)
await initGeoLibre();

// Compute NDVI from a COG
const result = await computeSpectralIndex(cogBytes, { index: 'NDVI' });
console.log(result.stats.mean);

// Interpolate sensor readings into a heatmap
const heatmap = await interpolateHeatmap([
  { x: -1.5, y: 42.3, value: 28 },
  { x: -1.49, y: 42.31, value: 32 },
  // ...
]);
```

## License

Apache-2.0

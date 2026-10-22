/**
 * Lazy WASM loader for geolibre-wasm.
 *
 * Regla de oro: el WASM nunca se carga al arrancar el módulo.
 * Solo se descarga la primera vez que se ejecuta una operación geoespacial.
 *
 * Modo 'light' (default): carga solo la browser library (4.3MB) para lecturas ligeras.
 * Modo 'full': carga el WASI runner (17MB) para tools completas.
 * Upgrade automático: si se llama a una tool en modo light, se hace upgrade a full.
 */

import { type WasmMode, type InitOptions, GeoError } from './types';

// Estado interno
let _ready = false;
let _mode: WasmMode | null = null;
let _initPromise: Promise<void> | null = null;

// Referencias a los módulos geolibre — import lazy
// Browser library (modo light)
type GeoLibreBrowser = typeof import('geolibre-wasm');
let _gl: GeoLibreBrowser | null = null;

// Tool runner (modo full)
type ToolRunner = typeof import('geolibre-wasm/tools');
let _tools: ToolRunner | null = null;

/**
 * Inicializa geolibre-wasm.
 * Llamada automáticamente por cada función pública.
 * Los módulos no deben llamarla explícitamente — se hace bajo demanda.
 */
export async function initGeoLibre(opts?: InitOptions): Promise<void> {
  if (_ready) return;
  if (_initPromise) return _initPromise;

  const mode = opts?.mode ?? 'light';

  _initPromise = (async () => {
    try {
      if (mode === 'full') {
        _tools = await import('geolibre-wasm/tools');
        // geolibre-wasm/tools no requiere init() explícito
      } else {
        _gl = await import('geolibre-wasm');
        await _gl.default();
      }
      _mode = mode;
      _ready = true;
    } catch (err) {
      _initPromise = null;
      throw new GeoError(
        'WASM_LOAD_FAILED',
        `Failed to load geolibre-wasm (${mode}): ${err instanceof Error ? err.message : String(err)}`
      );
    }
  })();

  return _initPromise;
}

/**
 * Upgrade from 'light' to 'full' mode on demand.
 *
 * Waits for any in-flight init to settle before resetting,
 * preventing races between concurrent light-init and full-upgrade calls.
 */
async function ensureFullMode(): Promise<void> {
  if (_ready && _mode === 'full') return;
  // Let any pending init settle first (prevents race light→full)  
  if (_initPromise) await _initPromise;
  if (_ready && _mode === 'full') return; // re-check after wait
  _ready = false;
  _mode = null;
  _initPromise = null;
  await initGeoLibre({ mode: 'full' });
}

/**
 * Get the browser library (wasm-bindgen GeoTiffReader, etc.).
 * Available in 'light' or 'full' mode.
 * Throws `WASM_NOT_INITIALIZED` if geolibre-wasm has not been loaded yet;
 * call `initGeoLibre()` first.
 */
export function getBrowserLib(): GeoLibreBrowser {
  if (!_ready) {
    throw new GeoError('WASM_NOT_INITIALIZED', 'geolibre-wasm not initialized. Call initGeoLibre() first.');
  }
  if (!_gl) {
    // In 'full' mode we have the tool runner but not the browser lib loaded.
    // Re-init in 'light' mode to make the browser lib available.
    throw new GeoError('WASM_NOT_INITIALIZED',
      'Browser library not available — geolibre-wasm was loaded in full/tools mode. ' +
      'Call initGeoLibre({ mode: "light" }) first if you need GeoTiffReader etc.');
  }
  return _gl;
}

/**
 * Run a WASI tool.
 * Automatically upgrades to 'full' mode if currently in 'light' mode.
 */
export async function runTool(
  toolId: string,
  args: string[],
  input: Record<string, Uint8Array>,
): Promise<{ files: Record<string, Uint8Array>; exitCode: number; stdout: string }> {
  await ensureFullMode();
  if (!_tools) {
    throw new GeoError('WASM_NOT_INITIALIZED', 'Tool runner not available after full init');
  }
  const result = await _tools.runTool(toolId, { args, input });
  if (result.exitCode !== 0) {
    throw new GeoError(
      'TOOL_EXECUTION_FAILED',
      `geolibre tool "${toolId}" failed (exit ${result.exitCode}): ${result.stdout.join('\n')}`
    );
  }
  return { files: result.files, exitCode: result.exitCode, stdout: result.stdout.join('\n') };
}

/**
 * Check if WASM is ready.
 */
export function isGeoLibreReady(): boolean {
  return _ready;
}

/**
 * List available tool IDs.
 */
export async function listTools(): Promise<string[]> {
  await ensureFullMode();
  if (!_tools) throw new GeoError('WASM_NOT_INITIALIZED', 'Tool runner not available');
  return _tools.listTools();
}

/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * useViewerLayer — read/write a single layer's toggle state from LayerRegistry.
 *
 * Used by a module's rendering component to know whether (and how opaque) to
 * draw its layer, and to report status back to the host's unified Layers panel.
 * HARD CUT: there is no module-local fallback — an id that was never
 * registered via `registerViewerLayers` returns inert defaults and never
 * touches the registry.
 */
import { useCallback, useSyncExternalStore } from 'react';
import { LayerRegistry, type ViewerLayerStatus } from './layerRegistry';

export interface UseViewerLayerReturn {
  visible: boolean;
  opacity: number;
  status: ViewerLayerStatus;
  setVisible: (visible: boolean) => void;
  setOpacity: (opacity: number) => void;
  setStatus: (status: ViewerLayerStatus) => void;
}

const INERT_DEFAULTS: Pick<UseViewerLayerReturn, 'visible' | 'opacity' | 'status'> = {
  visible: false,
  opacity: 100,
  status: 'idle',
};

function noop(): void {
  // Inert setter for an unregistered layer id — intentionally does nothing.
}

function warnUnknownLayer(id: string): void {
  if (!LayerRegistry.consumeUnknownWarning(id)) return;
  // eslint-disable-next-line no-console
  console.warn(
    `[@nekazari/sdk] useViewerLayer("${id}"): no layer registered with this id. ` +
      'Returning inert defaults. Register it first via defineModule({ viewerLayers: [...] }).',
  );
}

/**
 * Subscribe to a single viewer layer's live state.
 *
 * @param id the layer id declared in `defineModule({ viewerLayers: [{ id, ... }] })`
 */
export function useViewerLayer(id: string): UseViewerLayerReturn {
  const entry = useSyncExternalStore(
    LayerRegistry.subscribe,
    () => LayerRegistry.getLayer(id),
    () => LayerRegistry.getLayer(id),
  );

  const setVisible = useCallback((visible: boolean) => LayerRegistry.setVisible(id, visible), [id]);
  const setOpacity = useCallback((opacity: number) => LayerRegistry.setOpacity(id, opacity), [id]);
  const setStatus = useCallback((status: ViewerLayerStatus) => LayerRegistry.setStatus(id, status), [id]);

  if (!entry) {
    warnUnknownLayer(id);
    return {
      ...INERT_DEFAULTS,
      setVisible: noop,
      setOpacity: noop,
      setStatus: noop,
    };
  }

  return {
    visible: entry.visible,
    opacity: entry.opacity,
    status: entry.status,
    setVisible,
    setOpacity,
    setStatus,
  };
}

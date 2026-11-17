import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useEntitySelection } from '../useEntitySelection';

// Minimal Cesium double: the handler only stores the LEFT_CLICK action.
let clickAction: ((click: { position: unknown }) => void) | null;

class FakeHandler {
  setInputAction(action: (click: { position: unknown }) => void) { clickAction = action; }
  destroy() { /* noop */ }
}

const ownEntities = { name: 'viewer.entities' };
const moduleEntities = { name: 'module data source' };

function makeViewer(picked: unknown) {
  return {
    entities: ownEntities,
    scene: { canvas: {}, pick: vi.fn(() => picked) },
    isDestroyed: () => false,
  };
}

function clickOn(picked: unknown, onSelect: (entity: { id: string; type: string }) => void) {
  const viewerRef = { current: makeViewer(picked) };
  renderHook(() => useEntitySelection(viewerRef, 'view', onSelect));
  clickAction!({ position: { x: 1, y: 1 } });
}

describe('useEntitySelection', () => {
  beforeEach(() => {
    clickAction = null;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).Cesium = {
      ScreenSpaceEventHandler: FakeHandler,
      ScreenSpaceEventType: { LEFT_CLICK: 'LEFT_CLICK' },
      defined: (v: unknown) => v !== undefined && v !== null,
    };
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).Cesium;
  });

  it('selects an entity the viewer draws itself', () => {
    const onSelect = vi.fn();
    clickOn({ id: { id: 'parcel-urn:ngsi-ld:AgriParcel:p1', entityCollection: ownEntities } }, onSelect);
    expect(onSelect).toHaveBeenCalledWith({ id: 'urn:ngsi-ld:AgriParcel:p1', type: 'AgriParcel' });
  });

  it('leaves entities of a module data source to the module', () => {
    const onSelect = vi.fn();
    clickOn({ id: { id: 'urn:ngsi-ld:AgriManagementZone:p1:r1:L1', entityCollection: moduleEntities } }, onSelect);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('ignores clicks on nothing', () => {
    const onSelect = vi.fn();
    clickOn(undefined, onSelect);
    expect(onSelect).not.toHaveBeenCalled();
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const toggleSlots = { 'layer-toggle': [{ id: 't', component: 'T', priority: 1 }] } as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const panelSlots = { 'context-panel': [{ id: 'p', component: 'P', priority: 1 }] } as any;

let open = true;
const setLayersPanelOpen = vi.fn((v: boolean) => { open = v; });
const toggleModule = vi.fn();
let active = new Set<string>(['core', 'lidar']);

vi.mock('@/context/ViewerContext', () => ({
  useViewer: () => ({ isLayersPanelOpen: open, setLayersPanelOpen }),
}));
vi.mock('@/context/ModuleContext', () => ({
  useModules: () => ({
    modules: [
      { id: 'lidar', displayName: 'LiDAR', viewerSlots: toggleSlots },
      { id: 'risk', displayName: 'Riesgos', viewerSlots: panelSlots },
      { id: 'no-viewer', displayName: 'Nada' },
    ],
  }),
}));
vi.mock('@/context/SlotRegistry', () => ({
  useSlotRegistry: () => ({ isModuleActive: (id: string) => active.has(id), toggleModule }),
}));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));
vi.mock('@/components/viewer/CoreLayerToggles', () => ({ default: () => <div>core-toggles</div> }));
vi.mock('@/components/SlotRenderer', () => ({
  SlotRenderer: ({ moduleId }: { moduleId?: string }) => <div>{`options:${moduleId}`}</div>,
}));

import { ViewerLayersPanel } from '../ViewerLayersPanel';

beforeEach(() => {
  open = true;
  active = new Set(['core', 'lidar']);
  setLayersPanelOpen.mockClear();
  toggleModule.mockClear();
});

describe('ViewerLayersPanel', () => {
  it('lists core layers and one row per module with viewer widgets', () => {
    render(<ViewerLayersPanel />);
    expect(screen.getByText('core-toggles')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'LiDAR' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Riesgos' })).toBeInTheDocument();
    expect(screen.queryByText('Nada')).not.toBeInTheDocument();
  });

  it('reflects and toggles the module switch', () => {
    render(<ViewerLayersPanel />);
    expect(screen.getByRole('switch', { name: 'LiDAR' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Riesgos' })).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(screen.getByRole('switch', { name: 'Riesgos' }));
    expect(toggleModule).toHaveBeenCalledWith('risk');
  });

  it('offers options only for active modules that have a layer toggle', () => {
    render(<ViewerLayersPanel />);
    const expand = screen.getAllByRole('button', { name: 'viewer.layersModuleOptions' });
    expect(expand).toHaveLength(1);
    fireEvent.click(expand[0]);
    expect(screen.getByText('options:lidar')).toBeInTheDocument();
  });

  it('hides options when the module is switched off', () => {
    active = new Set(['core']);
    render(<ViewerLayersPanel />);
    expect(screen.queryByRole('button', { name: 'viewer.layersModuleOptions' })).not.toBeInTheDocument();
  });

  it('closes on Escape and toggles from the floating button', () => {
    const { rerender } = render(<ViewerLayersPanel />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(setLayersPanelOpen).toHaveBeenCalledWith(false);

    open = false;
    rerender(<ViewerLayersPanel />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'viewer.layersPanelOpen' }));
    expect(setLayersPanelOpen).toHaveBeenCalledWith(true);
  });

  it('toggles with Shift+L outside text inputs', () => {
    open = false;
    render(<ViewerLayersPanel />);
    fireEvent.keyDown(window, { key: 'L', shiftKey: true });
    expect(setLayersPanelOpen).toHaveBeenCalledWith(true);
  });
});

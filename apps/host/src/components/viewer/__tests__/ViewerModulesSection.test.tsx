import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const toggleSlots = { 'layer-toggle': [{ id: 't', component: 'T', priority: 1 }] } as any;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const panelSlots = { 'context-panel': [{ id: 'p', component: 'P', priority: 1 }] } as any;

const toggleModule = vi.fn();
let active = new Set<string>(['core', 'lidar']);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let modules: any[] = [];

vi.mock('@/context/ModuleContext', () => ({ useModules: () => ({ modules }) }));
vi.mock('@/context/SlotRegistry', () => ({
  useSlotRegistry: () => ({ isModuleActive: (id: string) => active.has(id), toggleModule }),
}));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));
vi.mock('@/components/SlotRenderer', () => ({
  SlotRenderer: ({ moduleId }: { moduleId?: string }) => <div>{`options:${moduleId}`}</div>,
}));

import { ViewerModulesSection } from '../ViewerModulesSection';

beforeEach(() => {
  active = new Set(['core', 'lidar']);
  toggleModule.mockClear();
  modules = [
    { id: 'lidar', displayName: 'LiDAR', viewerSlots: toggleSlots },
    { id: 'risk', displayName: 'Riesgos', viewerSlots: panelSlots },
    { id: 'no-viewer', displayName: 'Nada' },
  ];
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v); },
  });
});

describe('ViewerModulesSection', () => {
  it('lists one row per module with viewer widgets', () => {
    render(<ViewerModulesSection />);
    expect(screen.getByText('viewer.modulesSection.title')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'LiDAR' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Riesgos' })).toBeInTheDocument();
    expect(screen.queryByText('Nada')).not.toBeInTheDocument();
  });

  it('reflects and toggles the module switch', () => {
    render(<ViewerModulesSection />);
    expect(screen.getByRole('switch', { name: 'LiDAR' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Riesgos' })).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(screen.getByRole('switch', { name: 'Riesgos' }));
    expect(toggleModule).toHaveBeenCalledWith('risk');
  });

  it('offers layer options only for active modules with a layer toggle', () => {
    render(<ViewerModulesSection />);
    const expand = screen.getAllByRole('button', { name: 'viewer.modulesSection.options' });
    expect(expand).toHaveLength(1);
    fireEvent.click(expand[0]);
    expect(screen.getByText('options:lidar')).toBeInTheDocument();
  });

  it('collapses and remembers it', () => {
    render(<ViewerModulesSection />);
    fireEvent.click(screen.getByRole('button', { name: 'viewer.modulesSection.title' }));
    expect(screen.queryByRole('switch', { name: 'LiDAR' })).not.toBeInTheDocument();
    expect(localStorage.getItem('nkz.viewer.modulesSection.open')).toBe('false');
  });

  it('renders nothing when no module contributes to the viewer', () => {
    modules = [{ id: 'no-viewer', displayName: 'Nada' }];
    const { container } = render(<ViewerModulesSection />);
    expect(container).toBeEmptyDOMElement();
  });
});

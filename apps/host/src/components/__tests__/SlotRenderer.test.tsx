import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const A = () => <div>widget-a</div>;
const B = () => <div>widget-b</div>;

vi.mock('@/context/SlotRegistry', () => ({
  useSlotRegistryOptional: () => ({
    getVisibleWidgets: () => [
      { id: 'lidar-toggle', moduleId: 'lidar', component: 'A', priority: 1, localComponent: A },
      { id: 'soil-toggle', moduleId: 'soil', component: 'B', priority: 2, localComponent: B },
    ],
  }),
}));
vi.mock('@/context/ModuleContext', () => ({ useModules: () => ({ modules: [] }) }));
vi.mock('@/context/KeycloakAuthContext', () => ({ useAuth: () => ({ tenantProfile: null }) }));
vi.mock('@nekazari/module-kit', () => ({
  NKZProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  toNKZRegistration: () => ({}),
}));
vi.mock('@module-federation/runtime', () => ({ loadRemote: vi.fn() }));

import { SlotRenderer } from '../SlotRenderer';

describe('SlotRenderer', () => {
  it('renders every module when no moduleId is given', () => {
    render(<SlotRenderer slot="layer-toggle" />);
    expect(screen.getByText('widget-a')).toBeInTheDocument();
    expect(screen.getByText('widget-b')).toBeInTheDocument();
  });

  it('renders only the given module when moduleId is set', () => {
    render(<SlotRenderer slot="layer-toggle" moduleId="soil" />);
    expect(screen.queryByText('widget-a')).not.toBeInTheDocument();
    expect(screen.getByText('widget-b')).toBeInTheDocument();
  });
});

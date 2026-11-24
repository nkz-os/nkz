import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

let selectedEntityType = 'AgriSensor';
vi.mock('@/context/ViewerContext', () => ({
  useViewer: () => ({ selectedEntityId: 'urn:ngsi-ld:Device:acme:d1', selectedEntityType, clearSelection: vi.fn() }),
}));
vi.mock('@/context/ModuleContext', () => ({ useModules: () => ({ modules: [] }) }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k, language: 'en' }) }));
vi.mock('@/components/parcels/ParcelDetailsPanel', () => ({ ParcelDetailsPanel: () => null }));
vi.mock('@/components/Timeline/TimelineView', () => ({ TimelineView: () => null }));
vi.mock('@/hooks/useTelemetry', () => ({
  useTelemetry: () => ({
    latestTelemetry: null,
    isLoadingLatest: false,
    error: null,
    isConnected: false,
    getMeasurementValue: () => null,
    getMeasurementUnit: () => null,
    refreshLatest: vi.fn(),
  }),
}));

import CoreContextPanel from '../CoreContextPanel';

const base = { id: 'urn:ngsi-ld:Device:acme:d1', type: 'Device', name: { type: 'Property', value: 'Probe' } };

describe('CoreContextPanel official Device attributes', () => {
  it('renders an array controlledProperty as a comma list', () => {
    selectedEntityType = 'AgriSensor';
    render(<CoreContextPanel entityData={{ ...base, controlledProperty: { type: 'Property', value: ['humidity', 'temperature'] } }} />);
    expect(screen.getAllByText('humidity, temperature').length).toBeGreaterThan(0);
  });

  it('shows deviceState as the header status of a Device', () => {
    selectedEntityType = 'Device';
    render(<CoreContextPanel entityData={{ ...base, deviceState: { type: 'Property', value: 'working' }, status: 'error' }} />);
    const badges = screen.getAllByText('working').filter((el) => el.className.includes('rounded-full'));
    expect(badges).toHaveLength(1);
    expect(screen.getAllByText('error').filter((el) => el.className.includes('rounded-full'))).toHaveLength(0);
  });
});

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { UnifiedAsset } from '@/types/assets';

const parcel: UnifiedAsset = {
  id: 'urn:ngsi-ld:AgriParcel:p1',
  type: 'AgriParcel',
  name: 'North field',
  category: 'parcels',
  status: 'active',
  hasLocation: false,
  rawEntity: {},
};

const zone: UnifiedAsset = {
  id: 'urn:ngsi-ld:AgriParcelZone:p1:z1',
  type: 'AgriParcelZone',
  name: 'North quadrant',
  category: 'parcels',
  status: 'active',
  hasLocation: false,
  parentId: parcel.id,
  rawEntity: {},
};

const crop: UnifiedAsset = {
  id: 'urn:ngsi-ld:AgriCrop:c1',
  type: 'AgriCrop',
  name: 'Wheat',
  category: 'vegetation',
  status: 'active',
  hasLocation: false,
  rawEntity: {},
};

vi.mock('@/hooks/useAssets', () => ({
  useAssets: () => ({
    assets: [parcel, zone, crop],
    filteredAssets: [parcel, zone, crop],
    selectedAssets: new Set<string>(),
    isLoading: false,
    isRefreshing: false,
    error: null,
    filters: { categories: [], types: [], statuses: [], search: '' },
    setFilters: vi.fn(),
    resetFilters: vi.fn(),
    sort: { field: 'name', direction: 'asc' },
    setSort: vi.fn(),
    countsByCategory: {},
    countsByType: {},
    totalCount: 3,
    filteredCount: 3,
    toggleAsset: vi.fn(),
    selectAll: vi.fn(),
    deselectAll: vi.fn(),
    isSelected: () => false,
    refresh: vi.fn(),
    deleteAssets: vi.fn(),
    exportAssets: vi.fn(),
  }),
}));
vi.mock('@/context/ViewerContext', () => ({ useViewer: () => ({ selectEntity: vi.fn() }) }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/hooks/useEntityDependencies', () => ({
  useEntityDependencies: () => ({ checkDependenciesBatch: vi.fn(), shouldBlockDeletion: () => false, isChecking: false }),
}));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

import { AssetManagerGrid } from '../AssetManagerGrid';

describe('AssetManagerGrid context menu', () => {
  it('labels every action through i18n', () => {
    render(<AssetManagerGrid showCategoryNav={false} />);

    fireEvent.contextMenu(screen.getByText('Wheat'));

    expect(screen.getByText('entities.assets.menu.view_on_map')).toBeTruthy();
    expect(screen.getByText('entities.assets.assign.title…')).toBeTruthy();
    expect(screen.getByText('entities.assets.menu.copy_id')).toBeTruthy();
    expect(screen.getAllByText('entities.assets.delete').length).toBeGreaterThan(0);
    for (const hardcoded of ['Ver en mapa', 'Asignar a Parcela...', 'Copiar ID', 'Eliminar']) {
      expect(screen.queryByText(hardcoded)).toBeNull();
    }
  });

  it('offers only parcels as parents, not zones or other land entities', () => {
    render(<AssetManagerGrid showCategoryNav={false} />);

    const before = { parcel: screen.getAllByText('North field').length, zone: screen.getAllByText('North quadrant').length };

    fireEvent.contextMenu(screen.getByText('Wheat'));
    fireEvent.click(screen.getByText('entities.assets.assign.title…'));

    // The modal adds one option per eligible parent next to the list rows.
    expect(screen.getAllByText('North field')).toHaveLength(before.parcel + 1);
    expect(screen.getAllByText('North quadrant')).toHaveLength(before.zone);
  });
});

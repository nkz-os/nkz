import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { AssetCategory, UnifiedAsset } from '@/types/assets';

vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({
    t: (key: string, opts?: Record<string, unknown>) => (opts?.count !== undefined ? `${key}:${opts.count}` : key),
  }),
}));

import { AssetTreeView } from '../AssetTreeView';

function asset(id: string, name: string, category: AssetCategory, type: string, parentId?: string): UnifiedAsset {
  return { id, type, name, category, status: 'active', hasLocation: false, parentId, rawEntity: {} };
}

const props = {
  selectedAssets: new Set<string>(),
  onToggleSelect: vi.fn(),
  onAssetClick: vi.fn(),
  onContextMenu: vi.fn(),
};

describe('AssetTreeView', () => {
  it('shows a parcel with its weather station nested, and only unparented non-parcels as unassigned', () => {
    render(
      <AssetTreeView
        {...props}
        assets={[
          asset('p1', 'North field', 'parcels', 'AgriParcel'),
          asset('w1', 'virtual North field', 'weather', 'WeatherStation', 'p1'),
          asset('d1', 'Loose sensor', 'sensors', 'Device'),
        ]}
      />,
    );

    expect(screen.getByText('North field')).toBeTruthy();
    expect(screen.getByText('Loose sensor')).toBeTruthy();
    expect(screen.getByText('virtual North field')).toBeTruthy();
    expect(screen.getByText('entities.assets.tree.unassigned:1')).toBeTruthy();

    fireEvent.click(screen.getByText('entities.assets.tree.collapse_all'));
    expect(screen.queryByText('virtual North field')).toBeNull();
  });

  it('opens every parcel, sub-parcels included, so what hangs from them shows at once', () => {
    render(
      <AssetTreeView
        {...props}
        assets={[
          asset('p1', 'North field', 'parcels', 'AgriParcel'),
          asset('p2', 'North field, east half', 'parcels', 'AgriParcel', 'p1'),
          asset('c1', 'Rye', 'vegetation', 'AgriCrop', 'p2'),
          asset('z1', 'High wetness zone', 'parcels', 'AgriParcelZone', 'p1'),
        ]}
      />,
    );

    expect(screen.getByText('North field, east half')).toBeTruthy();
    expect(screen.getByText('Rye')).toBeTruthy();
    expect(screen.getByText('High wetness zone')).toBeTruthy();
  });

  it('renders the empty state through i18n', () => {
    render(<AssetTreeView {...props} assets={[]} />);
    expect(screen.getByText('entities.assets.tree.empty')).toBeTruthy();
  });
});

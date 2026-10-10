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
    expect(screen.queryByText('virtual North field')).toBeNull();
    expect(screen.getByText('entities.assets.tree.unassigned:1')).toBeTruthy();

    fireEvent.click(screen.getByText('entities.assets.tree.expand_all'));
    expect(screen.getByText('virtual North field')).toBeTruthy();
  });

  it('renders the empty state through i18n', () => {
    render(<AssetTreeView {...props} assets={[]} />);
    expect(screen.getByText('entities.assets.tree.empty')).toBeTruthy();
  });
});

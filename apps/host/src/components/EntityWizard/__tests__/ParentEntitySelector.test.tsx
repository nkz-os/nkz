import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const { getParentEntities } = vi.hoisted(() => ({ getParentEntities: vi.fn() }));
vi.mock('@/services/api', () => ({ default: { getParentEntities } }));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { ParentEntitySelector, type ParentEntity } from '../ParentEntitySelector';

const A: ParentEntity = { id: 'urn:ngsi-ld:AgriParcel:a', type: 'AgriParcel', name: 'Alpha', geometry: null };
const B: ParentEntity = { id: 'urn:ngsi-ld:AgriParcel:b', type: 'AgriParcel', name: 'Beta', geometry: null };

describe('ParentEntitySelector', () => {
  it('multiple mode toggles parcels and reports them in click order', async () => {
    getParentEntities.mockResolvedValue([A, B]);
    const onSelectMany = vi.fn();
    function Host() {
      const [ids, setIds] = useState<string[]>([]);
      return (
        <ParentEntitySelector
          entityType="AgriParcel"
          multiple
          selectedParentIds={ids}
          onSelectMany={(ps) => { onSelectMany(ps); setIds(ps.map((p) => p.id)); }}
          onSelect={vi.fn()}
        />
      );
    }
    render(<Host />);
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => screen.getByText('Beta'));
    fireEvent.click(screen.getByText('Beta'));
    fireEvent.click(screen.getByText('Alpha'));
    expect(onSelectMany).toHaveBeenLastCalledWith([B, A]);
    fireEvent.click(screen.getByText('Beta'));
    expect(onSelectMany).toHaveBeenLastCalledWith([A]);
  });

  it('single mode still calls onSelect', async () => {
    getParentEntities.mockResolvedValue([A, B]);
    const onSelect = vi.fn();
    render(<ParentEntitySelector entityType="AgriParcel" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button'));
    await waitFor(() => screen.getByText('Alpha'));
    fireEvent.click(screen.getByText('Alpha'));
    expect(onSelect).toHaveBeenCalledWith(A);
  });
});

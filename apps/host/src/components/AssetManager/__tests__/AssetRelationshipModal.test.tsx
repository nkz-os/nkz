/* eslint-disable @typescript-eslint/no-explicit-any */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { UnifiedAsset } from '@/types/assets';

const { mergeSDMEntity, deleteSDMEntityAttribute, updateSDMEntity, invalidateEntityCache } = vi.hoisted(() => ({
  invalidateEntityCache: vi.fn(),
  mergeSDMEntity: vi.fn(),
  deleteSDMEntityAttribute: vi.fn(),
  updateSDMEntity: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  default: { mergeSDMEntity, deleteSDMEntityAttribute, updateSDMEntity },
  invalidateEntityCache,
}));
vi.mock('@nekazari/ui-kit', async () => {
  const actual = await vi.importActual<typeof import('@nekazari/ui-kit')>('@nekazari/ui-kit');
  return {
    ...actual,
    Select: ({ value, onValueChange, options }: any) => (
      <select aria-label="owner" value={value} onChange={e => onValueChange(e.target.value)}>
        {options.map((o: any) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    ),
  };
});
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { AssetRelationshipModal } from '../AssetRelationshipModal';

const P1 = 'urn:ngsi-ld:AgriParcel:p1';
const P2 = 'urn:ngsi-ld:AgriParcel:p2';
const K = 'urn:ngsi-ld:AgriSolarPark:k';
const rel = (object: string) => ({ type: 'Relationship', object });

function parcel(id: string, name: string): UnifiedAsset {
  return { id, type: 'AgriParcel', name, category: 'parcels', status: 'active', hasLocation: false, rawEntity: {} };
}

function child(rawEntity: Record<string, unknown>, parentId?: string): UnifiedAsset {
  return {
    id: String(rawEntity.id),
    type: 'AgriCrop',
    name: 'Crop',
    category: 'vegetation',
    status: 'active',
    hasLocation: false,
    parentId,
    rawEntity,
  };
}

const parents = [parcel(P1, 'North field'), parcel(P2, 'South field')];

beforeEach(() => {
  mergeSDMEntity.mockReset().mockResolvedValue(undefined);
  deleteSDMEntityAttribute.mockReset().mockResolvedValue(undefined);
  updateSDMEntity.mockReset();
  invalidateEntityCache.mockReset();
});

describe('AssetRelationshipModal', () => {
  it('invalidates the asset type cache after a save, before onSuccess', async () => {
    const order: string[] = [];
    invalidateEntityCache.mockImplementation(() => order.push('invalidate'));
    const onSuccess = vi.fn(() => order.push('onSuccess'));
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:Device:d1' })}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
    );
    fireEvent.click(screen.getByText('South field'));
    fireEvent.click(screen.getByText('entities.assets.assign.save'));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(invalidateEntityCache).toHaveBeenCalledWith('AgriCrop');
    expect(order).toEqual(['invalidate', 'onSuccess']);
  });

  it('does not invalidate when the save fails', async () => {
    mergeSDMEntity.mockRejectedValue(new Error('boom'));
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:Device:d1' })}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('South field'));
    fireEvent.click(screen.getByText('entities.assets.assign.save'));
    await waitFor(() => expect(mergeSDMEntity).toHaveBeenCalled());
    await new Promise(r => setTimeout(r, 0));
    expect(invalidateEntityCache).not.toHaveBeenCalled();
  });

  it('assigns a parcel by merging hasAgriParcel into the entity', async () => {
    const onSuccess = vi.fn();
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:Device:d1' })}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
    );

    fireEvent.click(screen.getByText('South field'));
    fireEvent.click(screen.getByText('entities.assets.assign.save'));

    await waitFor(() =>
      expect(mergeSDMEntity).toHaveBeenCalledWith('urn:ngsi-ld:Device:d1', { hasAgriParcel: rel(P2) }),
    );
    expect(updateSDMEntity).not.toHaveBeenCalled();
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });

  it('removes a user-assigned parcel with DELETE, tolerating an attribute already gone', async () => {
    deleteSDMEntityAttribute.mockImplementation(async (_id: string, attribute: string) => {
      if (attribute === 'refParent') throw Object.assign(new Error('gone'), { response: { status: 404 } });
    });
    const onSuccess = vi.fn();
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:AgriCrop:c1', refAgriParcel: rel(P1), refParent: rel(P1) }, P1)}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
    );

    fireEvent.click(screen.getByText('entities.assets.assign.remove'));
    fireEvent.click(screen.getByText('entities.assets.assign.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(deleteSDMEntityAttribute).toHaveBeenCalledWith('urn:ngsi-ld:AgriCrop:c1', 'hasAgriParcel');
    expect(deleteSDMEntityAttribute).toHaveBeenCalledWith('urn:ngsi-ld:AgriCrop:c1', 'refParent');
    expect(mergeSDMEntity).not.toHaveBeenCalled();
  });

  it('shows an error and stays open when the broker rejects the write', async () => {
    mergeSDMEntity.mockRejectedValue(Object.assign(new Error('bad'), { response: { status: 400 } }));
    const onSuccess = vi.fn();
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:Device:d1' })}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
    );

    fireEvent.click(screen.getByText('North field'));
    fireEvent.click(screen.getByText('entities.assets.assign.save'));

    expect(await screen.findByText('entities.assets.assign.error')).toBeTruthy();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('does not offer removing a parcel the platform derives (per-parcel weather)', () => {
    render(
      <AssetRelationshipModal
        asset={child({ id: 'urn:ngsi-ld:WeatherObserved:w1', refParcel: rel(P1) }, P1)}
        potentialParents={parents}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />,
    );

    expect(screen.getByText('entities.assets.assign.current')).toBeTruthy();
    expect(screen.queryByText('entities.assets.assign.remove')).toBeNull();
  });

  describe('Device parcels', () => {
    const device = (urns: string[]): UnifiedAsset => ({
      ...child({ id: 'urn:ngsi-ld:Device:d1', controlledAsset: rel(urns as never) }, urns[0]),
      type: 'Device',
    });
    const renderDevice = (urns: string[], onSuccess = vi.fn()) =>
      render(
        <AssetRelationshipModal asset={device(urns)} potentialParents={parents} onClose={vi.fn()} onSuccess={onSuccess} />,
      );

    it('shows linked parcels checked, primary first, and saves one merge', async () => {
      const onSuccess = vi.fn();
      renderDevice([P1, P2], onSuccess);
      const boxes = screen.getAllByRole('checkbox');
      expect(boxes.map(b => b.getAttribute('aria-checked'))).toEqual(['true', 'true']);

      fireEvent.click(boxes[1]);
      fireEvent.click(screen.getByText('entities.assets.assign.save'));

      await waitFor(() => expect(onSuccess).toHaveBeenCalled());
      expect(mergeSDMEntity).toHaveBeenCalledTimes(1);
      expect(mergeSDMEntity).toHaveBeenCalledWith('urn:ngsi-ld:Device:d1', {
        controlledAsset: { type: 'Relationship', object: [P1] },
      });
      expect(deleteSDMEntityAttribute).not.toHaveBeenCalled();
    });

    it('blocks unchecking the last parcel', () => {
      renderDevice([P1]);
      const boxes = screen.getAllByRole('checkbox');
      expect(boxes[0].hasAttribute('disabled')).toBe(true);
      expect(screen.getByText('entities.assets.assign.last_parcel')).toBeTruthy();
    });

    it('shows an error after a failed save and does not retry', async () => {
      mergeSDMEntity.mockRejectedValue(new Error('net'));
      renderDevice([P1]);
      fireEvent.click(screen.getAllByRole('checkbox')[1]);
      fireEvent.click(screen.getByText('entities.assets.assign.save'));
      expect(await screen.findByText('entities.assets.assign.error')).toBeTruthy();
      expect(mergeSDMEntity).toHaveBeenCalledTimes(1);
    });

    describe('owner', () => {
      const park = (parcelUrn: string): UnifiedAsset => ({
        id: K,
        type: 'AgriSolarPark',
        name: 'East array',
        category: 'infrastructure',
        status: 'active',
        hasLocation: false,
        rawEntity: { id: K, hasAgriParcel: rel(parcelUrn) },
      });
      const renderOwned = (urns: string[], parks: UnifiedAsset[], onSuccess = vi.fn()) =>
        render(
          <AssetRelationshipModal
            asset={device(urns)}
            potentialParents={parents}
            solarParks={parks}
            onClose={vi.fn()}
            onSuccess={onSuccess}
          />,
        );

      it('shows the park as owner of a park-owned sensor; saving untouched makes no call', () => {
        renderOwned([K, P1], [park(P1)]);
        expect((screen.getByLabelText('owner') as HTMLSelectElement).value).toBe(K);
        expect(screen.getAllByRole('checkbox').map(b => b.getAttribute('aria-checked'))).toEqual(['true', 'false']);
        expect(screen.getByText('entities.assets.assign.save').closest('button')!.hasAttribute('disabled')).toBe(true);
        expect(mergeSDMEntity).not.toHaveBeenCalled();
      });

      it('switching the owner to the parcel saves one merge without the park', async () => {
        const onSuccess = vi.fn();
        renderOwned([K, P1], [park(P1)], onSuccess);
        fireEvent.change(screen.getByLabelText('owner'), { target: { value: 'parcel' } });
        fireEvent.click(screen.getByText('entities.assets.assign.save'));
        await waitFor(() => expect(onSuccess).toHaveBeenCalled());
        expect(mergeSDMEntity).toHaveBeenCalledTimes(1);
        expect(mergeSDMEntity).toHaveBeenCalledWith('urn:ngsi-ld:Device:d1', {
          controlledAsset: { type: 'Relationship', object: [P1] },
        });
      });

      it('choosing a park saves [park, ...parcels]', async () => {
        const onSuccess = vi.fn();
        renderOwned([P1], [park(P1)], onSuccess);
        fireEvent.change(screen.getByLabelText('owner'), { target: { value: K } });
        fireEvent.click(screen.getByText('entities.assets.assign.save'));
        await waitFor(() => expect(onSuccess).toHaveBeenCalled());
        expect(mergeSDMEntity).toHaveBeenCalledWith('urn:ngsi-ld:Device:d1', {
          controlledAsset: { type: 'Relationship', object: [K, P1] },
        });
      });

      it('drops the park owner when its parcel is unchecked', async () => {
        const onSuccess = vi.fn();
        renderOwned([K, P1, P2], [park(P1)], onSuccess);
        fireEvent.click(screen.getAllByRole('checkbox')[0]);
        fireEvent.click(screen.getByText('entities.assets.assign.save'));
        await waitFor(() => expect(onSuccess).toHaveBeenCalled());
        expect(mergeSDMEntity).toHaveBeenCalledWith('urn:ngsi-ld:Device:d1', {
          controlledAsset: { type: 'Relationship', object: [P2] },
        });
      });

      it('hides the owner choice when no park is on the selected parcels', () => {
        renderOwned([P2], [park(P1)]);
        expect(screen.queryByLabelText('owner')).toBeNull();
      });
    });
  });
});

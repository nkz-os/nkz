import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const PARCEL = 'urn:ngsi-ld:AgriParcel:tenant-a:p1';
const DEVICE = 'urn:ngsi-ld:Device:tenant-a:d1';

const { api } = vi.hoisted(() => ({
  api: {
    getSDMEntityInstance: vi.fn(),
    getSDMEntityInstances: vi.fn(),
    updateSDMEntity: vi.fn(),
    mergeSDMEntity: vi.fn(),
    deleteSDMEntityAttribute: vi.fn(),
    client: { get: vi.fn() },
  },
}));

vi.mock('@/services/api', () => ({ default: api }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (key: string) => key }) }));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { EntityEditorModal } from '../index';

function openEditor(entity: Record<string, unknown>, onSuccess = vi.fn()) {
  api.getSDMEntityInstance.mockResolvedValue(entity);
  render(
    <EntityEditorModal entityId={DEVICE} entityType="Device" isOpen onClose={vi.fn()} onSuccess={onSuccess} />,
  );
  return onSuccess;
}

const field = (labelKey: string) =>
  screen.getByText(labelKey).parentElement!.querySelector('input') as HTMLInputElement;

beforeEach(() => {
  for (const fn of [api.getSDMEntityInstance, api.updateSDMEntity, api.mergeSDMEntity, api.deleteSDMEntityAttribute]) {
    fn.mockReset();
  }
  api.mergeSDMEntity.mockResolvedValue(undefined);
  api.deleteSDMEntityAttribute.mockResolvedValue(undefined);
  api.client.get.mockReset();
});

describe('EntityEditorModal load', () => {
  it("shows the entity's current values once it has loaded", async () => {
    openEditor({ id: DEVICE, type: 'Device', name: { type: 'Property', value: 'Probe' } });

    await waitFor(() => expect(field('editor.field.name').value).toBe('Probe'));
  });
});

describe('EntityEditorModal save', () => {
  it('adds an attribute the entity does not have yet with a merge-patch', async () => {
    const onSuccess = openEditor({ id: DEVICE, type: 'Device', name: { type: 'Property', value: 'Probe' } });

    await waitFor(() => expect(screen.getByText('editor.field.description')).toBeTruthy());
    fireEvent.change(field('editor.field.description'), { target: { value: 'North gate' } });
    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(api.mergeSDMEntity).toHaveBeenCalledWith(DEVICE, {
      description: { type: 'Property', value: 'North gate' },
    });
    expect(api.updateSDMEntity).not.toHaveBeenCalled();
  });

  it('removes a cleared relationship with DELETE on the attribute', async () => {
    const onSuccess = openEditor({
      id: DEVICE,
      type: 'Device',
      refAgriParcel: { type: 'Relationship', object: PARCEL },
    });

    await waitFor(() => expect(screen.getByText('p1')).toBeTruthy());
    fireEvent.click(screen.getByText('p1').parentElement!.querySelector('button')!);
    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(api.deleteSDMEntityAttribute).toHaveBeenCalledWith(DEVICE, 'refAgriParcel');
    expect(api.mergeSDMEntity).not.toHaveBeenCalled();
  });

  it('saves a newly selected relationship', async () => {
    // The broker reads a bare q as "has an attribute with that name": it matches nothing.
    api.client.get.mockResolvedValue({ data: [] });
    api.getSDMEntityInstances.mockResolvedValue([
      { id: PARCEL, type: 'AgriParcel', name: { type: 'Property', value: 'North field' } },
      { id: 'urn:ngsi-ld:AgriParcel:tenant-a:p2', type: 'AgriParcel', name: { type: 'Property', value: 'South field' } },
    ]);
    const onSuccess = openEditor({ id: DEVICE, type: 'Device' });

    await waitFor(() => expect(screen.getByText('editor.field.refAgriParcel')).toBeTruthy());
    const relationshipField = screen.getByText('editor.field.refAgriParcel').parentElement!;
    fireEvent.click(relationshipField.querySelector('button')!);
    fireEvent.change(relationshipField.querySelector('input')!, { target: { value: 'North' } });
    fireEvent.click(await screen.findByText('North field'));
    expect(api.getSDMEntityInstances).toHaveBeenCalledWith('AgriParcel');
    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(api.mergeSDMEntity).toHaveBeenCalledWith(DEVICE, {
      refAgriParcel: { type: 'Relationship', object: PARCEL },
    });
  });

  it('searches relationship targets by name, ignoring case', async () => {
    api.getSDMEntityInstances.mockResolvedValue([
      { id: PARCEL, type: 'AgriParcel', name: { type: 'Property', value: 'North field' } },
      { id: 'urn:ngsi-ld:AgriParcel:tenant-a:p2', type: 'AgriParcel', name: { type: 'Property', value: 'South field' } },
    ]);
    openEditor({ id: DEVICE, type: 'Device' });

    await waitFor(() => expect(screen.getByText('editor.field.refAgriParcel')).toBeTruthy());
    const relationshipField = screen.getByText('editor.field.refAgriParcel').parentElement!;
    fireEvent.click(relationshipField.querySelector('button')!);
    fireEvent.change(relationshipField.querySelector('input')!, { target: { value: 'south' } });

    expect(await screen.findByText('South field')).toBeTruthy();
    expect(screen.queryByText('North field')).toBeNull();
  });

  it('treats an attribute already gone as removed', async () => {
    api.deleteSDMEntityAttribute.mockRejectedValue(Object.assign(new Error('gone'), { response: { status: 404 } }));
    const onSuccess = openEditor({
      id: DEVICE,
      type: 'Device',
      refAgriParcel: { type: 'Relationship', object: PARCEL },
    });

    await waitFor(() => expect(screen.getByText('p1')).toBeTruthy());
    fireEvent.click(screen.getByText('p1').parentElement!.querySelector('button')!);
    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
  });
});

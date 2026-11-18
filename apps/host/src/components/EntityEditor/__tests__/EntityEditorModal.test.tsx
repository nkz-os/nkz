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

const { parcelApi } = vi.hoisted(() => ({ parcelApi: { updateParcelAttributes: vi.fn() } }));

vi.mock('@/services/api', () => ({ default: api }));
vi.mock('@/services/parcelApi', () => ({ parcelApi }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (key: string) => key }) }));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { EntityEditorModal } from '../index';

function openEditor(entity: Record<string, unknown>, onSuccess = vi.fn()) {
  api.getSDMEntityInstance.mockResolvedValue(entity);
  render(
    <EntityEditorModal
      entityId={entity.id as string}
      entityType={entity.type as string}
      isOpen
      onClose={vi.fn()}
      onSuccess={onSuccess}
    />,
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
  parcelApi.updateParcelAttributes.mockReset();
  parcelApi.updateParcelAttributes.mockResolvedValue(undefined);
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

describe('EntityEditorModal parcel save', () => {
  it('saves a parcel through entity-manager, its only writer, in one call', async () => {
    const onSuccess = openEditor({
      id: PARCEL,
      type: 'AgriParcel',
      name: { type: 'Property', value: 'North field' },
      refAgriFarm: { type: 'Relationship', object: 'urn:ngsi-ld:AgriFarm:f1' },
    });

    await waitFor(() => expect(screen.getByText('f1')).toBeTruthy());
    fireEvent.change(field('editor.field.description'), { target: { value: 'Rye' } });
    fireEvent.click(screen.getByText('f1').parentElement!.querySelector('button')!);
    fireEvent.click(screen.getByText('common.save'));

    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(parcelApi.updateParcelAttributes).toHaveBeenCalledWith(
      PARCEL,
      { description: { type: 'Property', value: 'Rye' } },
      ['refAgriFarm'],
    );
    expect(api.mergeSDMEntity).not.toHaveBeenCalled();
    expect(api.deleteSDMEntityAttribute).not.toHaveBeenCalled();
  });

  it("shows entity-manager's reason when it rejects the change", async () => {
    parcelApi.updateParcelAttributes.mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        response: { status: 422, data: { error: 'A parcel keeps its geometry; location cannot be removed' } },
      }),
    );
    openEditor({ id: PARCEL, type: 'AgriParcel', name: { type: 'Property', value: 'North field' } });

    await waitFor(() => expect(field('editor.field.name').value).toBe('North field'));
    fireEvent.change(field('editor.field.name'), { target: { value: 'North' } });
    fireEvent.click(screen.getByText('common.save'));

    expect(await screen.findByText('A parcel keeps its geometry; location cannot be removed')).toBeTruthy();
  });
});

describe('EntityEditorModal machine geometry', () => {
  const MACHINE = 'urn:ngsi-ld:ManufacturingMachine:tenant-a:m1';

  it('shows the geometry of the machine category with its help, and keeps an unknown category visible', async () => {
    openEditor({ id: MACHINE, type: 'ManufacturingMachine', name: { type: 'Property', value: 'Drill' },
      category: { type: 'Property', value: 'implement' } });
    await waitFor(() => expect(screen.getByText('editor.field.mountPosition')).toBeTruthy());
    expect(screen.queryByText('editor.field.minTurningRadius')).toBeNull();
    expect(screen.getByText('editor.help.mountPosition')).toBeTruthy();
  });

  it('switches the geometry fields when the category changes', async () => {
    openEditor({ id: MACHINE, type: 'ManufacturingMachine', name: { type: 'Property', value: 'T' },
      category: { type: 'Property', value: 'implement' } });
    await waitFor(() => expect(screen.getByText('editor.field.mountPosition')).toBeTruthy());
    const select = screen.getByText('editor.field.category').parentElement!.querySelector('select') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'tractor' } });
    await waitFor(() => expect(screen.getByText('editor.field.minTurningRadius')).toBeTruthy());
    expect(screen.queryByText('editor.field.mountPosition')).toBeNull();
  });

  it('keeps a category value outside the list visible instead of showing it empty', async () => {
    openEditor({ id: MACHINE, type: 'ManufacturingMachine', name: { type: 'Property', value: 'H' },
      category: { type: 'Property', value: 'harvester' } });
    await waitFor(() => expect(screen.getByText('editor.field.category')).toBeTruthy());
    const select = screen.getByText('editor.field.category').parentElement!.querySelector('select') as HTMLSelectElement;
    expect(select.value).toBe('harvester');
  });

  it('keeps a stored value of the other role visible so it can be fixed or cleared', async () => {
    openEditor({ id: MACHINE, type: 'ManufacturingMachine', name: { type: 'Property', value: 'Old drill' },
      category: { type: 'Property', value: 'implement' },
      minTurningRadius: { type: 'Property', value: 6, unitCode: 'MTR' } });
    await waitFor(() => expect(screen.getByText('editor.field.minTurningRadius')).toBeTruthy());
    expect(screen.queryByText('editor.field.gpsOffsetX')).toBeNull();
  });

  it('clearing a number deletes the attribute instead of saving an empty value', async () => {
    const onSuccess = openEditor({ id: MACHINE, type: 'ManufacturingMachine', name: { type: 'Property', value: 'T' },
      category: { type: 'Property', value: 'tractor' },
      trackWidth: { type: 'Property', value: 1.9, unitCode: 'MTR' } });
    await waitFor(() => expect(field('editor.field.trackWidth').value).toBe('1.9'));
    fireEvent.change(field('editor.field.trackWidth'), { target: { value: '' } });
    fireEvent.click(screen.getByText('common.save'));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(api.deleteSDMEntityAttribute).toHaveBeenCalledWith(MACHINE, 'trackWidth');
    expect(api.mergeSDMEntity).not.toHaveBeenCalledWith(MACHINE, expect.objectContaining({ trackWidth: expect.anything() }));
  });
});


import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  listDataloggers: vi.fn(),
  rotateDatalogger: vi.fn(),
  getDataloggerConfig: vi.fn(),
  revokeDatalogger: vi.fn(),
  enableDatalogger: vi.fn(),
  deleteDatalogger: vi.fn(),
}));
const confirm = vi.hoisted(() => vi.fn());
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock('@/services/dataloggersApi', async () => {
  const actual = await vi.importActual<typeof import('@/services/dataloggersApi')>('@/services/dataloggersApi');
  return { ...actual, ...api };
});
vi.mock('@/context/ConfirmContext', () => ({ useConfirm: () => confirm }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => toast }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

import { DataloggersSettings } from '../DataloggersSettings';

const EMPTY = { id: 'dl-1', name: 'Field A', kind: 'datak', username: 'dl_a', status: 'active', devices: [] };
const BUSY = { id: 'dl-2', name: 'Orchard', kind: 'generic_mqtt', username: 'dl_b', status: 'revoked', devices: [{ externalId: 'probe_1', deviceUrn: 'urn:ngsi-ld:Device:tenant-a:probe_1' }] };

beforeEach(() => {
  Object.values(api).forEach(m => m.mockReset());
  confirm.mockReset().mockResolvedValue(true);
  api.listDataloggers.mockResolvedValue([EMPTY, BUSY]);
});

describe('DataloggersSettings', () => {
  it('lists name, kind, status and device names', async () => {
    render(<DataloggersSettings />);
    const row = await screen.findByTestId('datalogger-dl-2');
    expect(row).toHaveTextContent('Orchard');
    expect(row).toHaveTextContent('dataloggers.kind.generic_mqtt');
    expect(row).toHaveTextContent('dataloggers.status.revoked');
    expect(row).toHaveTextContent('probe_1');
  });

  it('delete disabled while devices remain', async () => {
    render(<DataloggersSettings />);
    const busy = await screen.findByTestId('datalogger-dl-2');
    const empty = await screen.findByTestId('datalogger-dl-1');
    expect(busy.querySelector('button[disabled]')).toHaveTextContent('dataloggers.delete.action');
    expect(empty.querySelector('button[disabled]')).toBeNull();
  });

  it('deleting an empty datalogger calls the API and reloads', async () => {
    api.deleteDatalogger.mockResolvedValue(undefined);
    render(<DataloggersSettings />);
    const row = await screen.findByTestId('datalogger-dl-1');
    fireEvent.click(Array.from(row.querySelectorAll('button')).find(b => b.textContent === 'dataloggers.delete.action')!);
    await waitFor(() => expect(api.deleteDatalogger).toHaveBeenCalledWith('dl-1'));
    await waitFor(() => expect(api.listDataloggers).toHaveBeenCalledTimes(2));
  });

  it('rotate shows the new password in a one-time modal and drops it on close', async () => {
    api.rotateDatalogger.mockResolvedValue({ password: 'pw-new' });
    api.getDataloggerConfig.mockResolvedValue({ mqtt: { host: 'broker.example', port: 8883, username: 'dl_a', password: null } });
    const { container } = render(<DataloggersSettings />);
    const row = await screen.findByTestId('datalogger-dl-1');
    fireEvent.click(Array.from(row.querySelectorAll('button')).find(b => b.textContent === 'dataloggers.rotate.action')!);
    expect(await screen.findByLabelText('dataloggers.credentials.password')).toHaveValue('pw-new');
    expect(screen.getByLabelText('dataloggers.credentials.host')).toHaveValue('broker.example');

    fireEvent.click(screen.getByText('dataloggers.credentials.saved'));
    await waitFor(() => expect(screen.queryByLabelText('dataloggers.credentials.password')).toBeNull());
    expect(container.innerHTML).not.toContain('pw-new');
    expect(document.body.innerHTML).not.toContain('pw-new');
  });

  it('does not rotate when the confirmation is declined', async () => {
    confirm.mockResolvedValue(false);
    render(<DataloggersSettings />);
    const row = await screen.findByTestId('datalogger-dl-1');
    fireEvent.click(Array.from(row.querySelectorAll('button')).find(b => b.textContent === 'dataloggers.rotate.action')!);
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(api.rotateDatalogger).not.toHaveBeenCalled();
  });

  it('revoke and enable follow the status', async () => {
    api.revokeDatalogger.mockResolvedValue(undefined);
    api.enableDatalogger.mockResolvedValue(undefined);
    render(<DataloggersSettings />);
    const active = await screen.findByTestId('datalogger-dl-1');
    fireEvent.click(Array.from(active.querySelectorAll('button')).find(b => b.textContent === 'dataloggers.revoke')!);
    await waitFor(() => expect(api.revokeDatalogger).toHaveBeenCalledWith('dl-1'));
    const revoked = await screen.findByTestId('datalogger-dl-2');
    fireEvent.click(Array.from(revoked.querySelectorAll('button')).find(b => b.textContent === 'dataloggers.enable')!);
    await waitFor(() => expect(api.enableDatalogger).toHaveBeenCalledWith('dl-2'));
  });
});

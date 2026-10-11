import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const { api, openEntityEditor } = vi.hoisted(() => ({
  api: { get: vi.fn().mockResolvedValue({ data: {} }), patch: vi.fn(), deleteSDMEntity: vi.fn() },
  openEntityEditor: vi.fn(),
}));

vi.mock('@/services/api', () => ({ default: api }));
vi.mock('@/components/EntityEditor', () => ({ openEntityEditor }));
vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (key: string) => key }) }));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { ManagementPanel } from '../ManagementPanel';

// What the entities page hands the inspector: a list row, not the NGSI-LD entity.
const row = { id: 'urn:ngsi-ld:Device:tenant-a:d1', type: 'Device', name: 'Probe', details: '80%', status: 'active' };

describe('ManagementPanel edit', () => {
  it('opens the entity editor on the entity, instead of editing the list row', () => {
    render(<ManagementPanel entity={row} onDelete={vi.fn()} />);

    fireEvent.click(screen.getByText('edit'));

    expect(openEntityEditor).toHaveBeenCalledWith(row.id, row.type);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

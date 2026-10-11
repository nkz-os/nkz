import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const { api, openEntityEditor } = vi.hoisted(() => ({
  api: { get: vi.fn().mockResolvedValue({ data: {} }), patch: vi.fn(), deleteSDMEntity: vi.fn() },
  openEntityEditor: vi.fn(),
}));

vi.mock('@/services/api', () => ({ default: api }));
vi.mock('@/components/EntityEditor', () => ({ openEntityEditor }));
vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string, params?: Record<string, unknown>) => i18n.t(key, { ns: 'common', ...params }) }),
}));
vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { ManagementPanel } from '../ManagementPanel';

beforeAll(async () => {
  const common = JSON.parse(readFileSync(resolve(__dirname, '../../../../public/locales/en/common.json'), 'utf8'));
  await i18n.use(initReactI18next).init({ lng: 'en', ns: ['common'], defaultNS: 'common', resources: { en: { common } } });
});

// What the entities page hands the inspector: a list row, not the NGSI-LD entity.
const row = { id: 'urn:ngsi-ld:Device:tenant-a:d1', type: 'Device', name: 'Probe', details: '80%', status: 'active' };

describe('ManagementPanel edit', () => {
  it('opens the entity editor on the entity, instead of editing the list row', () => {
    render(<ManagementPanel entity={row} onDelete={vi.fn()} />);

    fireEvent.click(screen.getByText('Edit'));

    expect(openEntityEditor).toHaveBeenCalledWith(row.id, row.type);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(api.patch).not.toHaveBeenCalled();
  });
});

describe('ManagementPanel texts', () => {
  it('renders its labels in the active language', () => {
    render(<ManagementPanel entity={{ ...row, online: true }} onDelete={vi.fn()} />);

    expect(screen.getByText('Properties')).toBeTruthy();
    expect(screen.getByText('Yes')).toBeTruthy();
  });

  it('asks for confirmation naming the entity before deleting', () => {
    render(<ManagementPanel entity={row} onDelete={vi.fn()} />);

    fireEvent.click(screen.getByText('Delete entity'));

    expect(screen.getByText('Are you sure?')).toBeTruthy();
    expect(screen.getAllByText('Probe').some(el => el.tagName === 'STRONG')).toBe(true);
    expect(screen.getByText('Cancel')).toBeTruthy();
    expect(screen.getByText('Confirm')).toBeTruthy();
  });
});

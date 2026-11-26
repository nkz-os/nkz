import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  listDataloggers: vi.fn(),
  rotateDatalogger: vi.fn(),
  getDataloggerConfig: vi.fn(),
  revokeDatalogger: vi.fn(),
  enableDatalogger: vi.fn(),
  deleteDatalogger: vi.fn(),
}));
const lang = vi.hoisted(() => ({ current: 'en' }));

vi.mock('@/services/dataloggersApi', async () => {
  const actual = await vi.importActual<typeof import('@/services/dataloggersApi')>('@/services/dataloggersApi');
  return { ...actual, ...api };
});
vi.mock('@/context/ConfirmContext', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }));
vi.mock('@/context/ToastContext', () => ({ useToastContext: () => ({ success: vi.fn(), error: vi.fn() }) }));
// The key and its parameters, so the tests see which line was chosen and with what.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => (o ? `${k} ${JSON.stringify(o)}` : k),
    i18n: { language: lang.current },
  }),
}));

import { DataloggersSettings, relativeTime } from '../DataloggersSettings';

const NOW = new Date('2026-11-26T12:00:00Z');
const GW = (lastSeen: string | null) => ({
  urn: 'urn:ngsi-ld:Device:tenant-a:gw-0123456789ab', lastSeen, rssi: null, softwareVersion: null,
});
const dl = (id: string, kind: string, gateway: unknown) => ({
  id, name: `Logger ${id}`, kind, username: `dl_${id}`, status: 'active', devices: [], gateway,
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  vi.setSystemTime(NOW);
  lang.current = 'en';
  Object.values(api).forEach((m) => m.mockReset());
});
afterEach(() => vi.useRealTimers());

describe('gateway line', () => {
  it('shows nothing at all when the gateway is null', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', null)]);
    render(<DataloggersSettings />);
    await screen.findByTestId('datalogger-a');
    expect(screen.queryByTestId('datalogger-gateway-a')).toBeNull();
    expect(screen.getByTestId('datalogger-a').textContent).not.toContain('dataloggers.gateway');
  });

  it('shows the last seen as a relative time', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('2026-11-26T11:55:00Z'))]);
    render(<DataloggersSettings />);
    const line = await screen.findByTestId('datalogger-gateway-a');
    expect(line).toHaveTextContent('dataloggers.gateway.last_seen {"time":"5 minutes ago"}');
  });

  it('says the beacon has not been received when it was never seen', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', GW(null))]);
    render(<DataloggersSettings />);
    expect(await screen.findByTestId('datalogger-gateway-a')).toHaveTextContent('dataloggers.gateway.not_received');
  });

  it('treats a date it cannot read as never received', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('not a date'))]);
    render(<DataloggersSettings />);
    expect(await screen.findByTestId('datalogger-gateway-a')).toHaveTextContent('dataloggers.gateway.not_received');
  });

  it('writes the time in the interface language', async () => {
    lang.current = 'es';
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('2026-11-26T11:55:00Z'))]);
    render(<DataloggersSettings />);
    expect(await screen.findByTestId('datalogger-gateway-a')).toHaveTextContent('hace 5 minutos');
  });

  it('one line per datalogger, each with its own state', async () => {
    api.listDataloggers.mockResolvedValue([
      dl('a', 'datak', GW('2026-11-26T11:00:00Z')), dl('b', 'datak', GW(null)), dl('c', 'datak', null),
    ]);
    render(<DataloggersSettings />);
    expect(await screen.findByTestId('datalogger-gateway-a')).toHaveTextContent('about 1 hour ago');
    expect(screen.getByTestId('datalogger-gateway-b')).toHaveTextContent('not_received');
    expect(screen.queryByTestId('datalogger-gateway-c')).toBeNull();
  });
});

describe('refresh and format', () => {
  it('writes the time in a <time> element with the instant', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('2026-11-26T11:55:00Z'))]);
    render(<DataloggersSettings />);
    const line = await screen.findByTestId('datalogger-gateway-a');
    expect(line.querySelector('time')).toHaveAttribute('dateTime', '2026-11-26T11:55:00Z');
  });

  it('a lastSeen in the future reads as just now', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('2026-11-26T12:00:30Z'))]);
    render(<DataloggersSettings />);
    expect(await screen.findByTestId('datalogger-gateway-a'))
      .toHaveTextContent('dataloggers.gateway.last_seen {"time":"dataloggers.gateway.just_now"}');
  });

  it('refreshes every 60 s and stops on unmount', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW('2026-11-26T11:55:00Z'))]);
    const { unmount } = render(<DataloggersSettings />);
    const line = await screen.findByTestId('datalogger-gateway-a');
    expect(line).toHaveTextContent('5 minutes ago');
    act(() => {
      vi.setSystemTime(new Date('2026-11-26T12:09:00Z'));
      vi.advanceTimersByTime(60_000);
    });
    expect(line).toHaveTextContent('15 minutes ago');
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('beacon instructions in the row', () => {
  const BEACON = { topic: '/KEY/gw-0123456789ab/attrs', intervalSeconds: 300 };
  const toggle = (id: string) =>
    screen.getByTestId(`datalogger-${id}`).querySelector('[data-testid="beacon-toggle"]') as HTMLElement | null;

  it('the toggle exists only for a generic_mqtt datalogger whose beacon was never received', async () => {
    api.listDataloggers.mockResolvedValue([
      dl('a', 'generic_mqtt', GW(null)), dl('b', 'generic_mqtt', GW('2026-11-26T11:55:00Z')),
      dl('c', 'datak', GW(null)), dl('d', 'generic_mqtt', null),
    ]);
    render(<DataloggersSettings />);
    await screen.findByTestId('datalogger-a');
    expect(toggle('a')).toHaveTextContent('dataloggers.beacon.show');
    expect(toggle('b')).toBeNull();
    expect(toggle('c')).toBeNull();
    expect(toggle('d')).toBeNull();
  });

  it('opening it reads the config (no password) and shows topic and message', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', GW(null))]);
    api.getDataloggerConfig.mockResolvedValue({ mqtt: {}, beacon: BEACON });
    render(<DataloggersSettings />);
    await screen.findByTestId('datalogger-a');
    fireEvent.click(toggle('a')!);
    const box = await screen.findByTestId('datalogger-beacon');
    expect(api.getDataloggerConfig).toHaveBeenCalledWith('a');
    expect(api.rotateDatalogger).not.toHaveBeenCalled();
    expect(screen.getByLabelText('dataloggers.beacon.topic')).toHaveValue('/KEY/gw-0123456789ab/attrs');
    expect(box).toHaveTextContent('{"uptime": <seconds since start, integer>}');
    expect(toggle('a')).toHaveTextContent('dataloggers.beacon.hide');
    fireEvent.click(toggle('a')!);
    expect(screen.queryByTestId('datalogger-beacon')).toBeNull();
  });

  it('a config without the beacon says it is not ready, never an empty panel', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', GW(null))]);
    api.getDataloggerConfig.mockResolvedValue({ mqtt: {} });
    render(<DataloggersSettings />);
    await screen.findByTestId('datalogger-a');
    fireEvent.click(toggle('a')!);
    expect(await screen.findByTestId('datalogger-beacon-not-ready')).toHaveTextContent('dataloggers.beacon.not_ready');
  });

  it('a failed config read says so', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', GW(null))]);
    api.getDataloggerConfig.mockRejectedValue(new Error('502'));
    render(<DataloggersSettings />);
    await screen.findByTestId('datalogger-a');
    fireEvent.click(toggle('a')!);
    expect(await screen.findByText('502')).toBeInTheDocument();
  });
});

describe('relativeTime', () => {
  it('is null for a date it cannot parse and falls back to English for an unknown language', () => {
    expect(relativeTime('nope', 'en')).toBeNull();
    expect(relativeTime('2026-11-26T11:55:00Z', 'xx')).toBe('5 minutes ago');
    expect(relativeTime('2026-11-26T11:55:00Z', undefined)).toBe('5 minutes ago');
    expect(relativeTime('2026-11-26T11:55:00Z', 'fr-FR')).toBe('il y a 5 minutes');
  });
});

describe('rotating a datalogger', () => {
  const rotate = async (id: string) => {
    const row = await screen.findByTestId(`datalogger-${id}`);
    fireEvent.click(Array.from(row.querySelectorAll('button')).find((b) => b.textContent === 'dataloggers.rotate.action')!);
  };
  const config = (beacon?: unknown) => ({ mqtt: { host: 'broker.example', port: 8883, username: 'dl_x', password: null }, ...(beacon ? { beacon } : {}) });
  const BEACON = { topic: '/KEY/gw-0123456789ab/attrs', intervalSeconds: 300 };

  it('a generic_mqtt datalogger gets the beacon topic and message', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', GW(null))]);
    api.rotateDatalogger.mockResolvedValue({ password: 'pw' });
    api.getDataloggerConfig.mockResolvedValue(config(BEACON));
    render(<DataloggersSettings />);
    await rotate('a');
    const box = await screen.findByTestId('datalogger-beacon');
    expect(screen.getByLabelText('dataloggers.beacon.topic')).toHaveValue('/KEY/gw-0123456789ab/attrs');
    expect(box).toHaveTextContent('{"uptime": <seconds since start, integer>}');
    expect(box).toHaveTextContent('dataloggers.beacon.intro {"seconds":300}');
    expect(box).toHaveTextContent('dataloggers.beacon.no_time_instant');
  });

  it('a datak datalogger gets no instructions', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'datak', GW(null))]);
    api.rotateDatalogger.mockResolvedValue({ password: 'pw' });
    api.getDataloggerConfig.mockResolvedValue(config(BEACON));
    render(<DataloggersSettings />);
    await rotate('a');
    await screen.findByLabelText('dataloggers.credentials.password');
    expect(screen.queryByTestId('datalogger-beacon')).toBeNull();
  });

  it('no beacon in the config, no instructions', async () => {
    api.listDataloggers.mockResolvedValue([dl('a', 'generic_mqtt', null)]);
    api.rotateDatalogger.mockResolvedValue({ password: 'pw' });
    api.getDataloggerConfig.mockResolvedValue(config());
    render(<DataloggersSettings />);
    await rotate('a');
    await waitFor(() => expect(screen.getByLabelText('dataloggers.credentials.password')).toHaveValue('pw'));
    expect(screen.queryByTestId('datalogger-beacon')).toBeNull();
  });
});

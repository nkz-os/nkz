/**
 * Component test: ParcelModulesPanel switch state must reflect real setup
 * outcome, not just user intent — a module with setup_status 'error' must
 * not render as ON next to its error badge (confusing: looks both broken
 * and active at once). Found via live production testing 2026-07-23.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ParcelModulesPanel } from '../ParcelModulesPanel';
import { parcelApi } from '@/services/parcelApi';

vi.mock('@/services/parcelApi', () => ({
  parcelApi: {
    getParcelModules: vi.fn(),
    activateParcelModule: vi.fn(),
    deactivateParcelModule: vi.fn(),
  },
}));

vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}));

vi.mock('@/context/ToastContext', () => ({
  useToastContext: () => ({ error: vi.fn(), success: vi.fn() }),
}));

vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => ({ hasAnyRole: () => true }),
}));

vi.mock('@/context/ModuleContext', () => ({
  useModules: () => ({
    modules: [
      {
        id: 'soil',
        name: 'soil',
        displayName: 'Soil',
        label: 'Soil',
        version: '1.0.0',
        routePath: '/soil',
        metadata: { setup_parcel_url: 'http://soil-module-service:8000/v1/soil/internal/setup-parcel' },
      },
    ],
  }),
}));

describe('ParcelModulesPanel', () => {
  it('renders the switch OFF when setup_status is error, even though enabled=true', async () => {
    vi.mocked(parcelApi.getParcelModules).mockResolvedValue([
      {
        module_id: 'soil',
        enabled: true,
        setup_status: 'error',
        last_error: 'HTTP 502',
        updated_at: null,
      },
    ]);

    render(<ParcelModulesPanel parcelId="urn:ngsi-ld:AgriParcel:t1:P1" />);

    const toggle = await screen.findByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('renders the switch ON when setup_status is ok', async () => {
    vi.mocked(parcelApi.getParcelModules).mockResolvedValue([
      {
        module_id: 'soil',
        enabled: true,
        setup_status: 'ok',
        last_error: null,
        updated_at: null,
      },
    ]);

    render(<ParcelModulesPanel parcelId="urn:ngsi-ld:AgriParcel:t1:P1" />);

    const toggle = await screen.findByRole('switch');
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });
});

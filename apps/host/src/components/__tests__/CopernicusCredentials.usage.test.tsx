/**
 * CopernicusCredentials — monthly satellite computation usage meter.
 * Fetches GET /api/vegetation/config/usage and renders the tenant's
 * quota so the cap is visible in the UI (never a silent limit).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { CopernicusCredentials } from '../CopernicusCredentials';

const mockGet = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock('@/services/api', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => ({
    user: { roles: ['TenantAdmin'] },
    tenantId: 'test-tenant',
  }),
}));

vi.mock('@/context/I18nContext', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'settings.copernicus.usage') {
        return `Cómputos satelitales este mes: ${params?.used} / ${params?.limit}`;
      }
      if (key === 'settings.copernicus.usageUnlimited') {
        return `Cómputos satelitales este mes: ${params?.used} / ∞`;
      }
      if (key === 'settings.copernicus.usageLimitReached') {
        return 'Límite mensual alcanzado';
      }
      return key;
    },
  }),
}));

// CopernicusCredentials uses useConfirm() (design-system confirm dialog) for
// its delete-credentials flow — not exercised by these usage-meter tests,
// but the hook throws outside a ConfirmProvider, so it needs a stub.
vi.mock('@/context/ConfirmContext', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(false),
}));

function mockConfigAndStatus() {
  mockGet.mockImplementation((url: string) => {
    if (url.includes('credentials-status')) {
      return Promise.resolve({
        data: { available: false, source: null, message: '', client_id_preview: null },
      });
    }
    if (url.includes('/config/usage')) {
      return Promise.resolve({ data: { used: 0, limit: null, remaining: null, period: '2026-07' } });
    }
    if (url.includes('/config')) {
      return Promise.resolve({
        data: { tenant_id: 'test-tenant', copernicus_client_id: null, copernicus_configured: false },
      });
    }
    return Promise.resolve({ data: {} });
  });
}

describe('CopernicusCredentials — usage meter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockConfigAndStatus();
  });

  it('shows used/limit for a finite quota', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/config/usage')) {
        return Promise.resolve({ data: { used: 12, limit: 100, remaining: 88, period: '2026-07' } });
      }
      if (url.includes('credentials-status')) {
        return Promise.resolve({
          data: { available: false, source: null, message: '', client_id_preview: null },
        });
      }
      return Promise.resolve({
        data: { tenant_id: 'test-tenant', copernicus_client_id: null, copernicus_configured: false },
      });
    });

    render(<CopernicusCredentials />);

    await waitFor(() => {
      expect(screen.getByText(/12 \/ 100/)).toBeInTheDocument();
    });
  });

  it('shows the unlimited marker when limit is null', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/config/usage')) {
        return Promise.resolve({ data: { used: 5, limit: null, remaining: null, period: '2026-07' } });
      }
      if (url.includes('credentials-status')) {
        return Promise.resolve({
          data: { available: false, source: null, message: '', client_id_preview: null },
        });
      }
      return Promise.resolve({
        data: { tenant_id: 'test-tenant', copernicus_client_id: null, copernicus_configured: false },
      });
    });

    render(<CopernicusCredentials />);

    await waitFor(() => {
      expect(screen.getByText(/5 \/ ∞/)).toBeInTheDocument();
    });
  });

  it('surfaces the limit-reached hint when remaining is 0', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes('/config/usage')) {
        return Promise.resolve({ data: { used: 100, limit: 100, remaining: 0, period: '2026-07' } });
      }
      if (url.includes('credentials-status')) {
        return Promise.resolve({
          data: { available: false, source: null, message: '', client_id_preview: null },
        });
      }
      return Promise.resolve({
        data: { tenant_id: 'test-tenant', copernicus_client_id: null, copernicus_configured: false },
      });
    });

    render(<CopernicusCredentials />);

    await waitFor(() => {
      expect(screen.getByText('Límite mensual alcanzado')).toBeInTheDocument();
    });
  });
});

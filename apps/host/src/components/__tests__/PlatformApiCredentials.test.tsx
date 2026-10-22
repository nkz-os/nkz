/**
 * PlatformApiCredentials — Copernicus CDSE fields are the OAuth client
 * pair (client_id/client_secret), never a plain username/password.
 * Guards: (1) the visible labels say "OAuth Client ID/Secret", not the old
 * "Username/Password" wording, and (2) the secret is never read back into
 * the input when credentials are already configured (write-only field).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { PlatformApiCredentials } from '../PlatformApiCredentials';

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock('@/services/api', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

vi.mock('@/context/KeycloakAuthContext', () => ({
  useAuth: () => ({
    user: { roles: ['PlatformAdmin'] },
    getToken: () => 'test-token',
  }),
}));

vi.mock('@nekazari/sdk', () => ({
  useTranslation: () => ({ t: (_key: string, def?: string) => def ?? _key }),
}));

describe('PlatformApiCredentials — Copernicus CDSE OAuth fields', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({ data: { configured: false } });
  });

  it('renders OAuth Client ID / OAuth Client Secret labels, not Username/Password', async () => {
    render(<PlatformApiCredentials />);

    await waitFor(() => expect(mockGet).toHaveBeenCalled());

    expect(screen.getByText(/OAuth Client ID/)).toBeInTheDocument();
    expect(screen.getByText(/OAuth Client Secret/)).toBeInTheDocument();
    expect(screen.queryByText(/Client ID \(Usuario\)/)).toBeNull();
    expect(screen.queryByText(/Client Secret \(Contraseña\)/)).toBeNull();
  });

  it('never reads the secret back into the input when already configured', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes('copernicus-cdse')) {
        return Promise.resolve({
          data: {
            configured: true,
            username: 'existing-client-id',
            url: 'https://dataspace.copernicus.eu',
          },
        });
      }
      return Promise.resolve({ data: { configured: false } });
    });

    render(<PlatformApiCredentials />);

    // Client ID IS shown back (it's not a secret) once credentials load.
    await waitFor(() => {
      expect(screen.getByDisplayValue('existing-client-id')).toBeInTheDocument();
    });

    // Client Secret input must stay empty — backend never returns it.
    const secretInput = screen.getByPlaceholderText(
      /dejar vacío para no cambiar/i
    ) as HTMLInputElement;
    expect(secretInput.value).toBe('');
  });
});

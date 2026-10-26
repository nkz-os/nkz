/**
 * The session cookie carries the access token and expires with it. Modules
 * authenticate with that cookie alone, so every refresh of the in-memory
 * token must re-seed the cookie. The proactive refresh in the request
 * interceptor runs before Keycloak's own expiry timer, which it also resets,
 * so it is the only refresh that happens while the host keeps calling the API.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

type Interceptor = (config: { headers: Record<string, string> }) => Promise<{ headers: Record<string, string> }>;

const { post, captured } = vi.hoisted(() => ({
  post: vi.fn(),
  captured: { request: null as Interceptor | null },
}));

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

vi.mock('axios', () => {
  const client = {
    get: vi.fn(),
    post,
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    request: vi.fn(),
    interceptors: {
      request: { use: vi.fn((fn: Interceptor) => { captured.request = fn; }) },
      response: { use: vi.fn() },
    },
  };
  return { default: { create: () => client }, create: () => client };
});

import { setKeycloakRef } from '../api';

function jwt(expiresInSeconds: number): string {
  const payload = { exp: Math.floor(Date.now() / 1000) + expiresInSeconds, tenant_id: 'acme' };
  return `h.${btoa(JSON.stringify(payload))}.s`;
}

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ status: 200, data: { ok: true } });
});

describe('request interceptor proactive refresh', () => {
  it('re-seeds the session cookie after refreshing a token about to expire', async () => {
    const fresh = jwt(300);
    const kc = { token: jwt(30), updateToken: vi.fn(async () => { kc.token = fresh; return true; }) };
    setKeycloakRef(kc);

    await captured.request!({ headers: {} });

    expect(kc.updateToken).toHaveBeenCalledWith(60);
    expect(post).toHaveBeenCalledWith('/api/auth/session', { token: fresh });
  });

  it('leaves the cookie alone when Keycloak did not refresh', async () => {
    const kc = { token: jwt(30), updateToken: vi.fn(async () => false) };
    setKeycloakRef(kc);

    await captured.request!({ headers: {} });

    expect(post).not.toHaveBeenCalled();
  });

  it('does nothing when the token is not close to expiry', async () => {
    const kc = { token: jwt(300), updateToken: vi.fn(async () => true) };
    setKeycloakRef(kc);

    await captured.request!({ headers: {} });

    expect(kc.updateToken).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

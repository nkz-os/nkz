import { describe, expect, it, vi } from 'vitest';

vi.mock('@/utils/logger', () => ({
  logger: { warn: vi.fn(), error: vi.fn(), debug: vi.fn(), info: vi.fn(), log: vi.fn() },
}));

import { createSessionRefresher } from '../sessionRefresh';

const now = () => Math.floor(Date.now() / 1000);

function setup(exp: number) {
  const kc = {
    token: 'old',
    tokenParsed: { exp },
    updateToken: vi.fn(async () => { kc.token = 'new'; return true; }),
  };
  const api = { setSession: vi.fn(async () => undefined) };
  const refresh = createSessionRefresher(() => kc, api);
  return { kc, api, refresh };
}

describe('createSessionRefresher', () => {
  it('refreshes an already expired token and re-seeds the cookie', async () => {
    const { kc, api, refresh } = setup(now() - 400);
    await refresh();
    expect(kc.updateToken).toHaveBeenCalledWith(30);
    expect(api.setSession).toHaveBeenCalledWith('new');
  });

  it('does nothing when the token is far from expiry', async () => {
    const { kc, api, refresh } = setup(now() + 3600);
    await refresh();
    expect(kc.updateToken).not.toHaveBeenCalled();
    expect(api.setSession).not.toHaveBeenCalled();
  });

  it('collapses back-to-back triggers into one refresh', async () => {
    const { kc, api, refresh } = setup(now() - 10);
    await Promise.all([refresh(), refresh()]);
    expect(kc.updateToken).toHaveBeenCalledTimes(1);
    expect(api.setSession).toHaveBeenCalledTimes(1);
  });

  it('swallows a rejected refresh and allows a later retry', async () => {
    const { kc, api, refresh } = setup(now() - 10);
    kc.updateToken.mockRejectedValueOnce(new Error('refresh token expired'));
    await expect(refresh()).resolves.toBeUndefined();
    expect(api.setSession).not.toHaveBeenCalled();
    await refresh();
    expect(api.setSession).toHaveBeenCalledTimes(1);
  });
});

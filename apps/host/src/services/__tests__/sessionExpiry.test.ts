/**
 * Every 401 path must end in the same place when the session is truly gone.
 *
 * Three interceptors handled 401 independently and disagreed: the SDK path
 * signalled `nekazari:session:expired` (App.tsx shows a notice and redirects,
 * preserving the current path), api.ts only rejected — leaving the page retrying
 * with no way out, which is the reported symptom — and parcelApi.ts hard-navigated
 * to /login, losing the return path and firing even when the token was still valid.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { notifySessionExpired, __resetSessionExpiryForTests } from '../sessionExpiry';

describe('notifySessionExpired', () => {
  let events: Event[];
  const capture = (e: Event) => events.push(e);

  beforeEach(() => {
    events = [];
    __resetSessionExpiryForTests();
    window.addEventListener('nekazari:session:expired', capture);
  });
  afterEach(() => {
    window.removeEventListener('nekazari:session:expired', capture);
    vi.useRealTimers();
  });

  it('signals the host instead of navigating, so the return path survives', () => {
    const before = window.location.href;
    notifySessionExpired('test');
    expect(events).toHaveLength(1);
    expect(window.location.href).toBe(before);
  });

  it('collapses a burst into one notice', () => {
    notifySessionExpired('a');
    notifySessionExpired('b');
    notifySessionExpired('c');
    expect(events).toHaveLength(1);
  });

  it('allows a new notice once the guard is reset', () => {
    notifySessionExpired('first');
    __resetSessionExpiryForTests();
    notifySessionExpired('second');
    expect(events).toHaveLength(2);
  });
});

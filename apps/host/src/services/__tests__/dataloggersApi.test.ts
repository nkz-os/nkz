import { describe, expect, it, vi } from 'vitest';
vi.mock('../api', () => ({ default: {} }));
import { apiErrorMessage } from '../dataloggersApi';

describe('apiErrorMessage', () => {
  it('appends the backend step when present', () => {
    expect(apiErrorMessage({ response: { data: { error: 'boom', step: 'acl' } } }, 'x')).toBe('boom (acl)');
    expect(apiErrorMessage({ response: { data: { error: 'boom' } } }, 'x')).toBe('boom');
    expect(apiErrorMessage({}, 'fallback')).toBe('fallback');
  });
});

import { useNKZRuntime } from '../runtime/NKZContext';
import type { UseAuthReturn } from './types';

/**
 * Returns the current authenticated user, tenant, and roles.
 * The host injects this via the NKZProvider; in `nkz dev` it comes from MockProvider.
 */
export function useAuth(): UseAuthReturn {
  return useNKZRuntime().auth;
}

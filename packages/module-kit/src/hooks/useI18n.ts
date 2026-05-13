import { useNKZRuntime } from '../runtime/NKZContext';
import type { UseI18nReturn } from './types';

/**
 * Returns the current language plus `t()` for translation.
 * The host's i18n bundle is augmented per-module from defineModule({ i18n })
 * during registration.
 */
export function useI18n(): UseI18nReturn {
  return useNKZRuntime().i18n;
}

/**
 * =============================================================================
 * Safe Numeric Formatting Utilities
 * =============================================================================
 *
 * All functions in this module handle null | undefined gracefully,
 * returning a human-readable placeholder instead of throwing.
 *
 * Usage:
 *   import { safeFixed } from '@/utils/format';
 *   <span>{safeFixed(metrics?.water_balance, 1)} mm</span>
 *
 * =============================================================================
 */

/** Fallback string shown when a numeric value is unavailable. */
const NO_DATA = '-';

/**
 * Format a number with fixed decimal places, safe against null/undefined.
 *
 * @param value  The number to format, or null/undefined
 * @param digits Number of decimal places (default: 1)
 * @returns      Formatted string or "-" if value is null/undefined
 */
export function safeFixed(
  value: number | null | undefined,
  digits: number = 1,
): string {
  if (value == null) return NO_DATA;
  return value.toFixed(digits);
}

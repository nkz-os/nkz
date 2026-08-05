/**
 * NGSI-LD entity helper functions.
 *
 * Canonical utilities for extracting display names and property values
 * from NGSI-LD entities (both normalized and simplified formats).
 */

/**
 * Extract human-readable display name from an NGSI-LD entity.
 *
 * Handles:
 * - Simplified format: entity.name is a string
 * - Normalized format: entity.name is { value: "..." }
 * - Fallback: entity.id
 */
export function getEntityDisplayName(entity: unknown): string {
  if (!entity || typeof entity !== 'object') return '';
  const e = entity as Record<string, unknown>;
  if (typeof e.name === 'string') return e.name;
  if (e.name && typeof e.name === 'object' && 'value' in e.name) {
    const value = (e.name as Record<string, unknown>).value;
    if (value != null) return String(value);
  }
  return typeof e.id === 'string' ? e.id : '';
}

/**
 * Extract a property value from an NGSI-LD entity attribute.
 *
 * Handles both `{ value: X }` (normalized) and plain `X` (simplified/keyValues).
 */
export function getNGSIValue<T = unknown>(attr: unknown): T | undefined {
  if (attr == null) return undefined;
  if (typeof attr === 'object' && 'value' in attr) return (attr as { value: T }).value;
  return attr as T;
}

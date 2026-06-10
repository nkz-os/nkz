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
export function getEntityDisplayName(entity: any): string {
  if (!entity) return '';
  if (typeof entity.name === 'string') return entity.name;
  if (entity.name?.value != null) return String(entity.name.value);
  return entity.id ?? '';
}

/**
 * Extract a property value from an NGSI-LD entity attribute.
 *
 * Handles both `{ value: X }` (normalized) and plain `X` (simplified/keyValues).
 */
export function getNGSIValue<T = any>(attr: any): T | undefined {
  if (attr == null) return undefined;
  if (typeof attr === 'object' && 'value' in attr) return attr.value as T;
  return attr as T;
}

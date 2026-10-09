/**
 * Entity relationship helpers for NGSI-LD entities
 */

export function isRelatedToParcel(entity: any, parcelId: string): boolean {
  if (!entity || !parcelId) return false;
  const attrs = ['hasAgriParcel', 'refAgriParcel', 'locatedAt', 'belongsTo', 'hasAgriFarm'];
  for (const attr of attrs) {
    const val = entity[attr];
    if (!val) continue;
    // NGSI-LD Relationship uses { type: 'Relationship', object: 'urn:...' }
    // Simplified/Normalized can be { value: 'urn:...' } or a plain string
    // Legacy flattened can be { id: 'urn:...' }
    const resolved = typeof val === 'object' && val?.value ? val.value : val;
    const targetId = typeof resolved === 'object'
      ? (resolved?.object || resolved?.id || String(resolved))
      : resolved;
    if (String(targetId) === parcelId) return true;
  }
  return false;
}

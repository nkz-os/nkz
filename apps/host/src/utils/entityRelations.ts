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

/**
 * Relationships that place an entity under a parent asset, in precedence order.
 * `hasAgriParcel` is canonical; `refAgriParcel` is the legacy alias of the same
 * attribute; `refParcel`/`locatedAt` carry per-parcel weather; `refParent` is the
 * legacy own attribute.
 */
export const PARENT_RELATIONSHIPS = [
  'hasAgriParcel',
  'refAgriParcel',
  'refParcel',
  'locatedAt',
  'refParent',
] as const;

export type ParentRelationship = (typeof PARENT_RELATIONSHIPS)[number];

export interface ParentReference {
  parentId: string;
  relationship: ParentRelationship;
}

const NGSI_LD_NULL = 'urn:ngsi-ld:null';

/** Target id of a relationship value in any NGSI-LD representation. */
function relationshipTarget(value: unknown): string | undefined {
  if (Array.isArray(value)) return relationshipTarget(value[0]);
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const v = value as { object?: unknown; value?: unknown };
    return relationshipTarget(v.object ?? v.value);
  }
  return undefined;
}

/** First parent relationship that names a target other than the entity itself. */
export function resolveParentRelationship(entity: unknown): ParentReference | undefined {
  if (!entity || typeof entity !== 'object') return undefined;
  const attrs = entity as Record<string, unknown>;
  for (const relationship of PARENT_RELATIONSHIPS) {
    const parentId = relationshipTarget(attrs[relationship]);
    if (parentId && parentId !== NGSI_LD_NULL && parentId !== attrs.id) {
      return { parentId, relationship };
    }
  }
  return undefined;
}

/** The parent relationships present on an entity, for projections that drop other attributes. */
export function pickParentRelationships(entity: unknown): Partial<Record<ParentRelationship, unknown>> {
  const picked: Partial<Record<ParentRelationship, unknown>> = {};
  if (!entity || typeof entity !== 'object') return picked;
  const attrs = entity as Record<string, unknown>;
  for (const relationship of PARENT_RELATIONSHIPS) {
    if (attrs[relationship] !== undefined) picked[relationship] = attrs[relationship];
  }
  return picked;
}

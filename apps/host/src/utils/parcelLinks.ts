/**
 * Parcel list of a Device: `controlledAsset`, always stored as a list
 * (the first URN is the primary parcel).
 */
import { relationshipTargets } from './entityRelations';

const NGSI_LD_NULL = 'urn:ngsi-ld:null';

/** Parcel URNs of an entity, in stored order; accepts string, `{object}` and lists. */
export function readParcelLinks(entity: unknown): string[] {
  if (!entity || typeof entity !== 'object') return [];
  const value = (entity as Record<string, unknown>).controlledAsset;
  return relationshipTargets(value).filter((urn) => urn && urn !== NGSI_LD_NULL);
}

/** Merge-patch fragment for `controlledAsset`: deduped, first-appearance order, never empty. */
export function parcelLinksFragment(urns: string[]): {
  controlledAsset: { type: 'Relationship'; object: string[] };
} {
  const object = Array.from(new Set(urns.filter(Boolean)));
  if (object.length === 0) {
    throw new Error('controlledAsset requires at least one parcel');
  }
  return { controlledAsset: { type: 'Relationship', object } };
}

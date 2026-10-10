// =============================================================================
// Parent assignment - broker operations to place an entity under a parcel
// =============================================================================
// In the platform @context `refAgriParcel` is an alias of `hasAgriParcel` (same
// attribute), so writing or deleting the canonical name covers both. `refParent`
// is a separate legacy attribute kept in step while it exists. `refParcel` and
// `locatedAt` are written by the platform (per-parcel weather) and never touched.
// Writes use merge-patch (PATCH /attrs cannot create attributes) and removal uses
// DELETE on the attribute, never a null value.

import { resolveParentRelationship } from '@/utils/entityRelations';

export type ParentChangeOperation =
  | { kind: 'merge'; fragment: Record<string, unknown> }
  | { kind: 'delete'; attribute: string };

const CANONICAL = 'hasAgriParcel';
const CANONICAL_NAMES = ['hasAgriParcel', 'refAgriParcel'];
const LEGACY_PARENT = 'refParent';

function has(entity: unknown, attribute: string): boolean {
  return !!entity && typeof entity === 'object' && (entity as Record<string, unknown>)[attribute] !== undefined;
}

const relationship = (object: string) => ({ type: 'Relationship', object });

export function planParentChange(entity: unknown, parcelId: string | null): ParentChangeOperation[] {
  if (parcelId) {
    const fragment: Record<string, unknown> = { [CANONICAL]: relationship(parcelId) };
    if (has(entity, LEGACY_PARENT)) fragment[LEGACY_PARENT] = relationship(parcelId);
    return [{ kind: 'merge', fragment }];
  }

  const operations: ParentChangeOperation[] = [];
  if (CANONICAL_NAMES.some(name => has(entity, name))) {
    operations.push({ kind: 'delete', attribute: CANONICAL });
  }
  if (has(entity, LEGACY_PARENT)) operations.push({ kind: 'delete', attribute: LEGACY_PARENT });
  return operations;
}

/** Whether the current parent comes from a relationship the user assigned. */
export function canRemoveParent(entity: unknown): boolean {
  const current = resolveParentRelationship(entity)?.relationship;
  return current !== undefined && (CANONICAL_NAMES.includes(current) || current === LEGACY_PARENT);
}

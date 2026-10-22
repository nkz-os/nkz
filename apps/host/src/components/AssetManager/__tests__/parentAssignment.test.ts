import { describe, expect, it } from 'vitest';
import { canRemoveParent, planParentChange } from '../parentAssignment';

const P1 = 'urn:ngsi-ld:AgriParcel:p1';
const P2 = 'urn:ngsi-ld:AgriParcel:p2';
const rel = (object: string) => ({ type: 'Relationship', object });

describe('planParentChange — assign', () => {
  it('writes the canonical relationship with a merge, never PATCH /attrs or null', () => {
    expect(planParentChange({ id: 'd1' }, P1)).toEqual([
      { kind: 'merge', fragment: { hasAgriParcel: rel(P1) } },
    ]);
  });

  it('overwrites the legacy alias through the canonical name (same attribute)', () => {
    expect(planParentChange({ id: 'c1', refAgriParcel: rel(P1) }, P2)).toEqual([
      { kind: 'merge', fragment: { hasAgriParcel: rel(P2) } },
    ]);
  });

  it('keeps an existing legacy refParent pointing at the same parcel', () => {
    expect(planParentChange({ id: 'c1', refAgriParcel: rel(P1), refParent: rel(P1) }, P2)).toEqual([
      { kind: 'merge', fragment: { hasAgriParcel: rel(P2), refParent: rel(P2) } },
    ]);
  });
});

describe('planParentChange — unassign', () => {
  it('deletes the canonical relationship when present under either name', () => {
    expect(planParentChange({ id: 'c1', refAgriParcel: rel(P1) }, null)).toEqual([
      { kind: 'delete', attribute: 'hasAgriParcel' },
    ]);
    expect(planParentChange({ id: 'c1', hasAgriParcel: rel(P1) }, null)).toEqual([
      { kind: 'delete', attribute: 'hasAgriParcel' },
    ]);
  });

  it('also deletes a legacy refParent', () => {
    expect(planParentChange({ id: 'c1', refAgriParcel: rel(P1), refParent: rel(P1) }, null)).toEqual([
      { kind: 'delete', attribute: 'hasAgriParcel' },
      { kind: 'delete', attribute: 'refParent' },
    ]);
  });

  it('never touches platform-written weather relationships', () => {
    expect(planParentChange({ id: 'w1', refParcel: rel(P1) }, null)).toEqual([]);
  });
});

describe('canRemoveParent', () => {
  it('allows removing a user-assigned parent', () => {
    expect(canRemoveParent({ id: 'c1', refAgriParcel: rel(P1) })).toBe(true);
    expect(canRemoveParent({ id: 'c1', refParent: P1 })).toBe(true);
  });

  it('does not offer removing a parent the platform derives', () => {
    expect(canRemoveParent({ id: 'w1', refParcel: rel(P1) })).toBe(false);
    expect(canRemoveParent({ id: 'd1' })).toBe(false);
  });
});

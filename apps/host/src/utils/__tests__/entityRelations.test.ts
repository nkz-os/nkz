import { describe, expect, it } from 'vitest';
import { pickParentRelationships, resolveParentRelationship } from '../entityRelations';

const P1 = 'urn:ngsi-ld:AgriParcel:p1';
const P2 = 'urn:ngsi-ld:AgriParcel:p2';
const rel = (object: unknown) => ({ type: 'Relationship', object });

describe('resolveParentRelationship', () => {
  it('reads a normalized Relationship', () => {
    expect(resolveParentRelationship({ id: 'e', hasAgriParcel: rel(P1) })).toEqual({
      parentId: P1,
      relationship: 'hasAgriParcel',
    });
  });

  it('reads keyValues strings and { value } wrappers', () => {
    expect(resolveParentRelationship({ id: 'e', refParent: P1 })?.parentId).toBe(P1);
    expect(resolveParentRelationship({ id: 'e', refAgriParcel: { value: P1 } })?.parentId).toBe(P1);
  });

  it('unwraps arrays, including a Relationship whose object is an array', () => {
    expect(resolveParentRelationship({ id: 'e', refParcel: [rel(P1)] })?.parentId).toBe(P1);
    expect(resolveParentRelationship({ id: 'e', locatedAt: rel([P2]) })?.parentId).toBe(P2);
  });

  it('follows the precedence hasAgriParcel > refAgriParcel > refParcel > locatedAt > refParent', () => {
    const entity = {
      id: 'e',
      refParent: rel('urn:ngsi-ld:AgriParcel:legacy'),
      locatedAt: rel('urn:ngsi-ld:AgriParcel:located'),
      refParcel: rel('urn:ngsi-ld:AgriParcel:weather'),
      refAgriParcel: rel(P2),
      hasAgriParcel: rel(P1),
    };
    expect(resolveParentRelationship(entity)).toEqual({ parentId: P1, relationship: 'hasAgriParcel' });
    const { hasAgriParcel: _h, ...withoutCanonical } = entity;
    expect(resolveParentRelationship(withoutCanonical)).toEqual({ parentId: P2, relationship: 'refAgriParcel' });
    expect(resolveParentRelationship({ id: 'e', refParent: rel(P1), refParcel: rel(P2) })).toEqual({
      parentId: P2,
      relationship: 'refParcel',
    });
  });

  it('skips empty, null-token and self references and falls through to the next relationship', () => {
    expect(
      resolveParentRelationship({
        id: 'e',
        hasAgriParcel: rel(''),
        refAgriParcel: 'urn:ngsi-ld:null',
        refParcel: rel('e'),
        refParent: P1,
      }),
    ).toEqual({ parentId: P1, relationship: 'refParent' });
  });

  it('returns undefined when no parent relationship is present', () => {
    expect(resolveParentRelationship({ id: 'e', name: 'x' })).toBeUndefined();
    expect(resolveParentRelationship(null)).toBeUndefined();
  });
});

describe('pickParentRelationships', () => {
  it('keeps only the parent relationships that are present', () => {
    expect(pickParentRelationships({ id: 'e', refParcel: rel(P1), name: 'x', hasAgriParcel: undefined })).toEqual({
      refParcel: rel(P1),
    });
  });
});

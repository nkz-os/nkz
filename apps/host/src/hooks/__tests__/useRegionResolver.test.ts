import { describe, it, expect } from 'vitest';
import { nextRegionOnMove } from '../useRegionResolver';

describe('nextRegionOnMove', () => {
  it('emits navarra when camera moves over Pamplona from eu', () => {
    expect(nextRegionOnMove(-1.64, 42.81, 'eu')).toBe('navarra');
  });

  it('emits spain when camera moves over Madrid from eu', () => {
    expect(nextRegionOnMove(-3.70, 40.42, 'eu')).toBe('spain');
  });

  it('emits null when still over Pamplona and already navarra', () => {
    expect(nextRegionOnMove(-1.64, 42.81, 'navarra')).toBeNull();
  });

  it('emits spain when camera moves from navarra to Madrid', () => {
    expect(nextRegionOnMove(-3.70, 40.42, 'navarra')).toBe('spain');
  });

  it('emits null when region does not change', () => {
    expect(nextRegionOnMove(-3.70, 40.42, 'spain')).toBeNull();
  });

  it('emits eu when camera moves from Spain to Paris', () => {
    expect(nextRegionOnMove(2.35, 48.85, 'spain')).toBe('eu');
  });

  it('emits world when camera is in the middle of the Atlantic', () => {
    expect(nextRegionOnMove(-40, 30, 'eu')).toBe('world');
  });

  it('emits null when staying in world', () => {
    expect(nextRegionOnMove(-40, 30, 'world')).toBeNull();
  });
});

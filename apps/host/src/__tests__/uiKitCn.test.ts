/**
 * `cn` from @nekazari/ui-kit decides which class wins when a component's own
 * variant classes and the caller's `className` set the same property. Without
 * it, the stylesheet order (alphabetical) decided, so a caller could not
 * override a variant colour.
 */
import { describe, expect, it } from 'vitest';
import { cn } from '@nekazari/ui-kit';

describe('ui-kit cn', () => {
  it('lets a later colour override an earlier one', () => {
    expect(cn('bg-nkz-accent-base text-nkz-text-on-accent', 'bg-nkz-surface text-nkz-info-strong')).toBe(
      'bg-nkz-surface text-nkz-info-strong',
    );
  });

  it('keeps a token font size next to a token text colour', () => {
    expect(cn('text-nkz-xs', 'text-nkz-text-primary')).toBe('text-nkz-xs text-nkz-text-primary');
  });

  it('treats token spacing, radius and duration as their own scales', () => {
    expect(cn('px-nkz-stack rounded-nkz-md duration-nkz-fast', 'px-4 rounded-xl duration-200')).toBe(
      'px-4 rounded-xl duration-200',
    );
  });

  it('keeps variant-prefixed classes separate from base ones', () => {
    expect(cn('hover:bg-nkz-surface-sunken', 'bg-nkz-info-soft')).toBe('hover:bg-nkz-surface-sunken bg-nkz-info-soft');
  });
});

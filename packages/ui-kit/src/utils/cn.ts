/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import { clsx, type ClassValue } from 'clsx';
import { extendTailwindMerge } from 'tailwind-merge';

/*
 * Components put the caller's `className` after their own variant classes. Plain
 * concatenation does not make it win: when two utilities set the same property,
 * the one emitted later in the stylesheet applies, and Tailwind orders them by
 * name, not by position in the class string. tailwind-merge drops the earlier
 * conflicting class instead.
 *
 * The design-token scales are registered so they are classified correctly:
 * without this, `text-nkz-xs` (a font size) would be read as a text colour and
 * removed next to `text-nkz-text-primary`.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      spacing: ['nkz-tight', 'nkz-inline', 'nkz-stack', 'nkz-section'],
      borderRadius: ['nkz-xs', 'nkz-sm', 'nkz-md', 'nkz-lg', 'nkz-xl', 'nkz-2xl', 'nkz-full'],
    },
    classGroups: {
      'font-size': [{ text: ['nkz-2xs', 'nkz-xs', 'nkz-sm', 'nkz-base', 'nkz-md', 'nkz-lg', 'nkz-xl', 'nkz-2xl', 'nkz-3xl'] }],
      shadow: [{ shadow: ['nkz-sm', 'nkz-md', 'nkz-lg', 'nkz-xl', 'nkz-inset-highlight'] }],
      z: [{ z: ['nkz-base', 'nkz-map-overlay', 'nkz-toolbar', 'nkz-rail', 'nkz-header', 'nkz-popover', 'nkz-tooltip', 'nkz-modal', 'nkz-toast', 'nkz-loading'] }],
      duration: [{ duration: ['nkz-fast', 'nkz-normal', 'nkz-slow', 'nkz-reduced'] }],
      ease: [{ ease: ['nkz-default', 'nkz-spring'] }],
    },
  },
});

/** Joins class names and lets later utilities override earlier conflicting ones. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

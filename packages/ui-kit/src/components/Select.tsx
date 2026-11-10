/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import { useCallback, useRef, useState } from 'react';
import * as RadixSelect from '@radix-ui/react-select';
import { cn } from '../utils/cn';

type SelectSize = 'sm' | 'md';

interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface SelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  size?: SelectSize;
  error?: boolean;
  disabled?: boolean;
  className?: string;
}

const sizeTriggerClasses: Record<SelectSize, string> = {
  sm: 'h-7 px-nkz-inline text-nkz-xs',
  md: 'h-9 px-nkz-stack text-nkz-sm',
};

const scrollHintClasses =
  'pointer-events-none absolute inset-x-0 flex items-center justify-center h-5 text-nkz-text-muted bg-nkz-surface-raised';

type ScrollEdges = { up: boolean; down: boolean };
const NO_EDGES: ScrollEdges = { up: false, down: false };

/**
 * Which way the list can still scroll. Not Radix's ScrollUp/DownButton: when one of
 * those mounts it scrolls the focused item back into view, and on touch the focus
 * stays on the first item, so the first swipe down jumped the list back to the top.
 */
function useScrollEdges() {
  const [edges, setEdges] = useState<ScrollEdges>(NO_EDGES);
  const detach = useRef<(() => void) | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    detach.current?.();
    detach.current = null;
    if (!el) {
      setEdges(NO_EDGES);
      return;
    }
    const update = () => {
      const max = el.scrollHeight - el.clientHeight;
      const next = { up: el.scrollTop > 1, down: el.scrollTop < max - 1 };
      setEdges((prev) => (prev.up === next.up && prev.down === next.down ? prev : next));
    };
    update();
    el.addEventListener('scroll', update, { passive: true });
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(el);
    detach.current = () => {
      el.removeEventListener('scroll', update);
      observer?.disconnect();
    };
  }, []);
  return [ref, edges] as const;
}

function Chevron({ up = false }: { up?: boolean }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
      <path
        d={up ? 'M3 7l3-3 3 3' : 'M3 5l3 3 3-3'}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder = 'Select...',
  size = 'md',
  error,
  disabled,
  className,
}: SelectProps) {
  const [viewportRef, edges] = useScrollEdges();
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RadixSelect.Trigger
        className={cn(
          'inline-flex items-center justify-between w-full gap-nkz-inline',
          'bg-nkz-surface border rounded-nkz-md text-nkz-text-primary',
          'transition-colors duration-nkz-fast',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          sizeTriggerClasses[size],
          error && 'border-nkz-danger',
          !error && 'border-nkz-border hover:border-nkz-border-strong',
          className
        )}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon className="text-nkz-text-muted">
          <Chevron />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content
          position="popper"
          sideOffset={4}
          className={cn(
            'z-nkz-dropdown bg-nkz-surface-raised border border-nkz-border rounded-nkz-md shadow-nkz-lg',
            'relative overflow-hidden min-w-[var(--radix-select-trigger-width)]',
            // Radix makes the viewport scrollable; without a height cap a long list
            // overflows the screen and items below the fold are unreachable.
            'max-h-[var(--radix-select-content-available-height)]'
          )}
        >
          <RadixSelect.Viewport ref={viewportRef} className="p-nkz-tight">
            {options.map((opt) => (
              <RadixSelect.Item
                key={opt.value}
                value={opt.value}
                disabled={opt.disabled}
                className={cn(
                  'relative flex items-center px-nkz-inline py-nkz-tight text-nkz-sm rounded-nkz-sm',
                  'text-nkz-text-primary cursor-pointer select-none',
                  'focus:outline-none focus-visible:bg-nkz-surface-sunken data-[highlighted]:bg-nkz-surface-sunken',
                  'data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed'
                )}
              >
                <RadixSelect.ItemText>{opt.label}</RadixSelect.ItemText>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
          {/* Radix hides the scrollbar: these show there is more above or below. */}
          {edges.up && (
            <div aria-hidden data-direction="up" className={cn(scrollHintClasses, 'top-0')}>
              <Chevron up />
            </div>
          )}
          {edges.down && (
            <div aria-hidden data-direction="down" className={cn(scrollHintClasses, 'bottom-0')}>
              <Chevron />
            </div>
          )}
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}

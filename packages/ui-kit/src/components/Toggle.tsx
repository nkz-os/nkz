/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import * as RadixToggle from '@radix-ui/react-toggle';
import clsx from 'clsx';

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  size?: 'sm' | 'md';
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
  size = 'md',
}: ToggleProps) {
  return (
    <div className="flex items-center gap-nkz-inline">
      <RadixToggle.Root
        pressed={checked}
        onPressedChange={onChange}
        disabled={disabled}
        className={clsx(
          'rounded-nkz-full transition-colors duration-nkz-fast',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:ring-offset-2',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          size === 'sm' ? 'w-8 h-4' : 'w-10 h-5',
          checked ? 'bg-nkz-accent-base' : 'bg-nkz-border'
        )}
      >
        <span
          className={clsx(
            'block rounded-full bg-white shadow-sm transition-transform duration-nkz-fast',
            size === 'sm' ? 'w-3 h-3' : 'w-4 h-4',
            checked
              ? size === 'sm'
                ? 'translate-x-4'
                : 'translate-x-5'
              : 'translate-x-0.5'
          )}
        />
      </RadixToggle.Root>
      {label && (
        <span className="text-nkz-sm text-nkz-text-primary select-none">
          {label}
        </span>
      )}
    </div>
  );
}

/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import * as RadixCheckbox from '@radix-ui/react-checkbox';
import clsx from 'clsx';

interface CheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  id?: string;
}

export function Checkbox({ checked, onChange, label, disabled, id }: CheckboxProps) {
  const checkId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

  return (
    <div className="flex items-center gap-nkz-inline">
      <RadixCheckbox.Root
        id={checkId}
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
        className={clsx(
          'flex items-center justify-center w-4 h-4 rounded-nkz-xs border',
          'transition-colors duration-nkz-fast',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:ring-offset-2',
          'disabled:opacity-50 disabled:cursor-not-allowed',
          checked
            ? 'bg-nkz-accent-base border-nkz-accent-base'
            : 'bg-nkz-surface border-nkz-border hover:border-nkz-border-strong'
        )}
      >
        <RadixCheckbox.Indicator className="text-nkz-text-on-accent">
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
            <path
              d="M2 5l2 2 4-4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {label && (
        <label
          htmlFor={checkId}
          className={clsx(
            'text-nkz-sm text-nkz-text-primary select-none cursor-pointer',
            disabled && 'opacity-50 cursor-not-allowed'
          )}
        >
          {label}
        </label>
      )}
    </div>
  );
}

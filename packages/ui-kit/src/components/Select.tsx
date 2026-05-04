/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import * as RadixSelect from '@radix-ui/react-select';
import clsx from 'clsx';

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
  return (
    <RadixSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RadixSelect.Trigger
        className={clsx(
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
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M3 5l3 3 3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content
          position="popper"
          sideOffset={4}
          className={clsx(
            'z-nkz-dropdown bg-nkz-surface-raised border border-nkz-border rounded-nkz-md shadow-nkz-lg',
            'overflow-hidden min-w-[var(--radix-select-trigger-width)]'
          )}
        >
          <RadixSelect.Viewport className="p-nkz-tight">
            {options.map((opt) => (
              <RadixSelect.Item
                key={opt.value}
                value={opt.value}
                disabled={opt.disabled}
                className={clsx(
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
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}

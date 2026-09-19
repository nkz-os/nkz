/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import * as RadixSwitch from '@radix-ui/react-switch';
import clsx from 'clsx';

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  labelPosition?: 'left' | 'right';
  disabled?: boolean;
}

export function Switch({
  checked,
  onChange,
  label,
  labelPosition = 'right',
  disabled,
}: SwitchProps) {
  const switchEl = (
    <RadixSwitch.Root
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      className={clsx(
        'w-9 h-5 rounded-nkz-full relative transition-colors duration-nkz-fast',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:ring-offset-2',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        checked ? 'bg-nkz-accent-base' : 'bg-nkz-border'
      )}
    >
      <RadixSwitch.Thumb
        // Knob deliberately does NOT follow bg-nkz-surface: in dark
        // profiles that token is dark, so the knob would blend into its
        // own (also dark) track — fails 1.4.11 in every profile we
        // measured. Its boundary is carried by elevation (shadow-sm)
        // instead of color, per WCAG 1.4.11's own allowance.
        className={clsx(
          'block w-4 h-4 bg-white rounded-full shadow-sm transition-transform duration-nkz-fast',
          checked ? 'translate-x-[18px]' : 'translate-x-[2px]'
        )}
      />
    </RadixSwitch.Root>
  );

  if (!label) return switchEl;

  return (
    <label className="flex items-center gap-nkz-inline cursor-pointer select-none">
      {labelPosition === 'left' && (
        <span className="text-nkz-sm text-nkz-text-primary">{label}</span>
      )}
      {switchEl}
      {labelPosition === 'right' && (
        <span className="text-nkz-sm text-nkz-text-primary">{label}</span>
      )}
    </label>
  );
}

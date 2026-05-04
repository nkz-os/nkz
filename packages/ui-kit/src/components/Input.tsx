/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type InputSize = 'sm' | 'md';

interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> {
  size?: InputSize;
  error?: boolean;
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ size = 'md', error, prefix, suffix, className, disabled, ...props }, ref) => {
    return (
      <div className="relative flex items-center">
        {prefix && (
          <span
            className={clsx(
              'absolute left-0 flex items-center justify-center text-nkz-text-muted pointer-events-none',
              size === 'sm' ? 'w-7 h-7' : 'w-9 h-9'
            )}
          >
            {prefix}
          </span>
        )}
        <input
          ref={ref}
          disabled={disabled}
          className={clsx(
            'w-full bg-nkz-surface border rounded-nkz-md text-nkz-text-primary',
            'transition-colors duration-nkz-fast',
            'placeholder:text-nkz-text-muted',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:border-nkz-accent-base',
            'disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-nkz-surface-sunken',
            size === 'sm' && 'h-7 px-nkz-inline text-nkz-xs',
            size === 'md' && 'h-9 px-nkz-stack text-nkz-sm',
            error && 'border-nkz-danger focus-visible:ring-nkz-danger',
            !error && 'border-nkz-border hover:border-nkz-border-strong',
            prefix && (size === 'sm' ? 'pl-8' : 'pl-10'),
            suffix && (size === 'sm' ? 'pr-8' : 'pr-10'),
            className
          )}
          {...props}
        />
        {suffix && (
          <span
            className={clsx(
              'absolute right-0 flex items-center justify-center text-nkz-text-muted pointer-events-none',
              size === 'sm' ? 'w-7 h-7' : 'w-9 h-9'
            )}
          >
            {suffix}
          </span>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';

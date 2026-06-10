/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  'aria-label': string;
  children: React.ReactNode;
  variant?: 'ghost' | 'secondary';
  size?: 'sm' | 'md';
  active?: boolean;
}

export function IconButton({
  variant = 'ghost',
  size = 'md',
  active,
  className,
  children,
  ...props
}: IconButtonProps) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center rounded-nkz-md',
        'transition-colors duration-nkz-fast',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base',
        size === 'sm' ? 'w-7 h-7' : 'w-8 h-8',
        variant === 'ghost' &&
          'text-nkz-text-secondary hover:bg-nkz-surface-sunken hover:text-nkz-text-primary',
        variant === 'secondary' &&
          'text-nkz-text-primary bg-nkz-surface-sunken hover:bg-nkz-border border border-nkz-border',
        active && 'text-nkz-accent-base bg-nkz-accent-soft',
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

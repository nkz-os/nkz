/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';
import { useHMI } from '../context/HMIContext';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  accent?: boolean;
  loading?: boolean;
  leadingIcon?: React.ReactNode;
  trailingIcon?: React.ReactNode;
  href?: string;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'bg-nkz-accent-base text-nkz-text-on-accent hover:bg-nkz-accent-strong',
  secondary:
    'bg-nkz-surface-sunken text-nkz-text-primary hover:bg-nkz-border border border-nkz-border',
  ghost: 'text-nkz-text-secondary hover:bg-nkz-surface-sunken hover:text-nkz-text-primary',
  danger: 'bg-nkz-danger text-white hover:bg-nkz-danger-strong',
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-7 px-nkz-inline text-nkz-xs gap-nkz-tight',
  md: 'h-9 px-nkz-stack text-nkz-sm gap-nkz-inline',
  lg: 'h-11 px-nkz-section text-nkz-base gap-nkz-inline',
};

export function Button({
  variant = 'primary',
  size = 'md',
  accent = false,
  loading = false,
  leadingIcon,
  trailingIcon,
  className,
  disabled,
  children,
  href,
  ...props
}: ButtonProps) {
  const { isHmiMode } = useHMI();

  const classes = clsx(
    'inline-flex items-center justify-center font-medium',
    'transition-colors duration-nkz-fast',
    'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:ring-offset-2',
    'disabled:opacity-50 disabled:cursor-not-allowed',
    isHmiMode
      ? 'rounded-sm min-h-[64px] min-w-[64px] px-6 py-4 text-lg uppercase tracking-wide border-2'
      : clsx('rounded-nkz-md', sizeClasses[size]),
    isHmiMode
      ? variant === 'primary'
        ? 'bg-nkz-accent-base text-white border-white hover:bg-nkz-accent-strong'
        : variant === 'secondary'
          ? 'bg-nkz-surface-sunken text-nkz-text-primary border-nkz-border hover:bg-nkz-surface'
          : variant === 'ghost'
            ? 'bg-transparent text-nkz-text-secondary border-transparent hover:border-nkz-border-strong'
            : 'bg-nkz-danger text-white border-white hover:bg-nkz-danger-strong border-2'
      : !accent
        ? variantClasses[variant]
        : variant === 'primary' &&
          'bg-nkz-accent-base text-nkz-text-on-accent hover:bg-nkz-accent-strong',
    className
  );

  const content = (
    <>
      {loading ? <span className="animate-spin">{'⟳'}</span> : leadingIcon}
      {children}
      {trailingIcon}
    </>
  );

  if (href) {
    return (
      <a href={href} className={classes}>
        {content}
      </a>
    );
  }

  return (
    <button className={classes} disabled={disabled || loading} {...props}>
      {content}
    </button>
  );
}

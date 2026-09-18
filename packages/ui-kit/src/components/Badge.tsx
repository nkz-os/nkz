/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type BadgeIntent = 'default' | 'positive' | 'warning' | 'negative' | 'info';

interface BadgeProps {
  intent?: BadgeIntent;
  children: React.ReactNode;
  className?: string;
}

const intentClasses: Record<BadgeIntent, string> = {
  default: 'bg-nkz-surface-sunken text-nkz-text-secondary border border-nkz-border',
  positive: 'bg-nkz-success-soft text-nkz-success-strong border border-nkz-success-strong',
  warning: 'bg-nkz-warning-soft text-nkz-warning-strong border border-nkz-warning-strong',
  negative: 'bg-nkz-danger-soft text-nkz-danger-strong border border-nkz-danger-strong',
  info: 'bg-nkz-info-soft text-nkz-info-strong border border-nkz-info-strong',
};

export function Badge({ intent = 'default', children, className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-nkz-full px-nkz-inline py-0.5',
        'text-nkz-xs font-medium leading-4',
        intentClasses[intent],
        className
      )}
    >
      {children}
    </span>
  );
}

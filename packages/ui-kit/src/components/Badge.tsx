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
  positive: 'bg-green-100 text-green-800 border border-green-200',
  warning: 'bg-amber-100 text-amber-800 border border-amber-200',
  negative: 'bg-red-100 text-red-800 border border-red-200',
  info: 'bg-blue-100 text-blue-800 border border-blue-200',
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

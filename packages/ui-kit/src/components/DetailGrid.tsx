/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

/* ───────── DetailGrid ───────── */

interface DetailGridProps {
  children: React.ReactNode;
  columns?: 1 | 2 | 3;
  className?: string;
}

const gridColumns: Record<NonNullable<DetailGridProps['columns']>, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
};

export function DetailGrid({ children, columns = 2, className }: DetailGridProps) {
  return (
    <div className={clsx('grid gap-nkz-stack', gridColumns[columns], className)}>
      {children}
    </div>
  );
}

/* ───────── DetailItem ───────── */

interface DetailItemProps {
  label: string;
  value: React.ReactNode;
  className?: string;
}

export function DetailItem({ label, value, className }: DetailItemProps) {
  return (
    <div className={clsx('flex flex-col gap-0.5', className)}>
      <span className="text-nkz-xs text-nkz-text-secondary font-medium uppercase tracking-wider">
        {label}
      </span>
      <span className="text-nkz-sm text-nkz-text-primary font-medium">
        {value}
      </span>
    </div>
  );
}

/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type MetricGridColumns = 2 | 3 | 4 | 6;

interface MetricGridProps {
  columns?: MetricGridColumns;
  children: React.ReactNode;
  className?: string;
}

const gridCols: Record<MetricGridColumns, string> = {
  2: 'grid-cols-1 sm:grid-cols-2',
  3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
  4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  6: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6',
};

export function MetricGrid({ columns = 4, children, className }: MetricGridProps) {
  return (
    <div
      className={clsx(
        'grid gap-nkz-stack',
        gridCols[columns],
        className
      )}
    >
      {children}
    </div>
  );
}

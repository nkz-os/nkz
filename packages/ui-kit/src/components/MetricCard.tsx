/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import clsx from 'clsx';

type TrendDirection = 'up' | 'down' | 'neutral';

interface MetricCardProps {
  label: string;
  value: string | number;
  unit?: string;
  trend?: {
    direction: TrendDirection;
    value: string;
  };
  accentColor?: string;
  className?: string;
}

const trendColors: Record<TrendDirection, string> = {
  up: 'text-green-600',
  down: 'text-red-600',
  neutral: 'text-nkz-text-muted',
};

export function MetricCard({
  label,
  value,
  unit,
  trend,
  accentColor,
  className,
}: MetricCardProps) {
  return (
    <div
      className={clsx(
        'relative bg-nkz-surface border border-nkz-border rounded-nkz-md p-nkz-stack',
        'flex flex-col gap-nkz-tight',
        className
      )}
    >
      {accentColor && (
        <div
          className="absolute left-0 top-0 bottom-0 w-1 rounded-l-nkz-md"
          style={{ backgroundColor: accentColor }}
        />
      )}
      <span className="text-nkz-xs text-nkz-text-secondary font-medium uppercase tracking-wider">
        {label}
      </span>
      <div className="flex items-baseline gap-nkz-tight">
        <span className="text-nkz-2xl font-semibold text-nkz-text-primary leading-tight">
          {value}
        </span>
        {unit && (
          <span className="text-nkz-sm text-nkz-text-muted">{unit}</span>
        )}
      </div>
      {trend && (
        <span className={clsx('text-nkz-xs font-medium', trendColors[trend.direction])}>
          {trend.direction === 'up' && '↑ '}
          {trend.direction === 'down' && '↓ '}
          {trend.value}
        </span>
      )}
    </div>
  );
}

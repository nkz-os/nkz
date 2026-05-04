/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import clsx from 'clsx';

type ProgressSize = 'sm' | 'md';
type ProgressIntent = 'default' | 'positive' | 'warning' | 'negative';

interface ProgressBarProps {
  value: number;
  size?: ProgressSize;
  intent?: ProgressIntent;
  showLabel?: boolean;
  className?: string;
}

const sizeClasses: Record<ProgressSize, string> = {
  sm: 'h-1.5',
  md: 'h-2.5',
};

const intentClasses: Record<ProgressIntent, string> = {
  default: 'bg-nkz-accent-base',
  positive: 'bg-green-500',
  warning: 'bg-amber-500',
  negative: 'bg-red-500',
};

export function ProgressBar({
  value,
  size = 'md',
  intent = 'default',
  showLabel = false,
  className,
}: ProgressBarProps) {
  const clampedValue = Math.max(0, Math.min(100, value));

  return (
    <div className={clsx('flex flex-col gap-nkz-tight', className)}>
      {showLabel && (
        <div className="flex justify-between text-nkz-xs text-nkz-text-secondary">
          <span>{Math.round(clampedValue)}%</span>
        </div>
      )}
      <div
        className={clsx(
          'w-full bg-nkz-surface-sunken rounded-nkz-full overflow-hidden',
          sizeClasses[size]
        )}
        role="progressbar"
        aria-valuenow={clampedValue}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={clsx(
            'h-full rounded-nkz-full transition-all duration-nkz-fast',
            intentClasses[intent]
          )}
          style={{ width: `${clampedValue}%` }}
        />
      </div>
    </div>
  );
}

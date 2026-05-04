/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import clsx from 'clsx';

type SkeletonVariant = 'text' | 'circle' | 'rect';

interface SkeletonProps {
  variant?: SkeletonVariant;
  className?: string;
  width?: string | number;
  height?: string | number;
}

const variantClasses: Record<SkeletonVariant, string> = {
  text: 'h-4 rounded-nkz-sm',
  circle: 'rounded-full',
  rect: 'rounded-nkz-md',
};

export function Skeleton({
  variant = 'text',
  className,
  width,
  height,
}: SkeletonProps) {
  return (
    <div
      className={clsx(
        'bg-nkz-border animate-pulse',
        variantClasses[variant],
        className
      )}
      style={{
        width: width ?? (variant === 'text' ? '100%' : undefined),
        height: height ?? (variant === 'circle' ? '40px' : variant === 'rect' ? '80px' : undefined),
      }}
    />
  );
}

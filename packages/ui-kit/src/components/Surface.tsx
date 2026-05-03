/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

type SurfaceVariant = 'default' | 'raised' | 'sunken';
type SurfacePadding = 'none' | 'tight' | 'inline' | 'stack' | 'section';
type SurfaceRadius = 'none' | 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl';

interface SurfaceProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: SurfaceVariant;
  padding?: SurfacePadding;
  radius?: SurfaceRadius;
  as?: React.ElementType;
}

const variantMap: Record<SurfaceVariant, string> = {
  default: 'bg-nkz-surface border border-nkz-border',
  raised: 'bg-nkz-surface-raised border border-nkz-border shadow-nkz-md',
  sunken: 'bg-nkz-surface-sunken border border-nkz-border',
};

const paddingMap: Record<SurfacePadding, string> = {
  none: '',
  tight: 'p-nkz-tight',
  inline: 'p-nkz-inline',
  stack: 'p-nkz-stack',
  section: 'p-nkz-section',
};

const radiusMap: Record<SurfaceRadius, string> = {
  none: '',
  xs: 'rounded-nkz-xs',
  sm: 'rounded-nkz-sm',
  md: 'rounded-nkz-md',
  lg: 'rounded-nkz-lg',
  xl: 'rounded-nkz-xl',
  '2xl': 'rounded-nkz-2xl',
};

export function Surface({
  variant = 'default',
  padding = 'none',
  radius = 'md',
  className,
  as: Tag = 'div',
  ...props
}: SurfaceProps) {
  return (
    <Tag
      className={clsx(variantMap[variant], paddingMap[padding], radiusMap[radius], className)}
      {...(props as React.HTMLAttributes<HTMLElement>)}
    />
  );
}

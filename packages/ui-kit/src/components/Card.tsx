/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';
import { useHMI } from '../context/HMIContext';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padding?: 'sm' | 'md' | 'lg' | 'none';
}

const paddingMap: Record<NonNullable<CardProps['padding']>, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6'
};

export const Card: React.FC<CardProps> = ({ className, padding = 'md', children, ...props }) => {
  const { isHmiMode } = useHMI();

  return (
    <div
      className={clsx(
        isHmiMode 
          ? 'rounded-none border-4 border-gray-600 bg-gray-900 text-white shadow-none' 
          : 'rounded-lg border border-gray-200 bg-white text-gray-900 shadow-sm',
        paddingMap[padding],
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
};


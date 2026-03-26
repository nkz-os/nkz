/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';
import { useHMI } from '../context/HMIContext';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost';
}

const base = 'inline-flex items-center justify-center font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2';

// Standard Web Theme
const standardBase = 'rounded-md px-3 py-2 text-sm';
const standardVariants: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-green-600 text-white hover:bg-green-700 focus:ring-green-500',
  secondary: 'bg-gray-100 text-gray-900 hover:bg-gray-200 focus:ring-gray-400',
  ghost: 'bg-transparent text-gray-700 hover:bg-gray-100 focus:ring-gray-300'
};

// ISO 11783-6 HMI Theme (Opaque, High Contrast, 48x48 min touch area)
const hmiBase = 'rounded-sm px-6 py-4 text-lg min-h-[64px] min-w-[64px] border-2 uppercase tracking-wide';
const hmiVariants: Record<NonNullable<ButtonProps['variant']>, string> = {
  primary: 'bg-green-700 text-white border-white hover:bg-green-600 active:bg-green-800 focus:ring-white',
  secondary: 'bg-gray-800 text-white border-gray-400 hover:bg-gray-700 active:bg-gray-900 focus:ring-gray-400',
  ghost: 'bg-black text-white border-transparent hover:border-gray-500 focus:ring-gray-500'
};

export const Button: React.FC<ButtonProps> = ({ variant = 'primary', className, children, ...props }) => {
  const { isHmiMode } = useHMI();

  return (
    <button
      className={clsx(
        base, 
        isHmiMode ? hmiBase : standardBase,
        isHmiMode ? hmiVariants[variant] : standardVariants[variant],
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
};


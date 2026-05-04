/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ error, className, disabled, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        disabled={disabled}
        className={clsx(
          'w-full bg-nkz-surface border rounded-nkz-md text-nkz-text-primary',
          'transition-colors duration-nkz-fast resize-y min-h-[80px]',
          'placeholder:text-nkz-text-muted',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base focus-visible:border-nkz-accent-base',
          'disabled:opacity-50 disabled:cursor-not-allowed disabled:bg-nkz-surface-sunken',
          'px-nkz-stack py-nkz-inline text-nkz-sm',
          error && 'border-nkz-danger focus-visible:ring-nkz-danger',
          !error && 'border-nkz-border hover:border-nkz-border-strong',
          className
        )}
        {...props}
      />
    );
  }
);

Textarea.displayName = 'Textarea';

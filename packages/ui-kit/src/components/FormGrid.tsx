/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

/* ───────── FormGrid ───────── */

interface FormGridProps {
  children: React.ReactNode;
  columns?: 1 | 2 | 3;
  className?: string;
}

const gridColumns: Record<NonNullable<FormGridProps['columns']>, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 md:grid-cols-2',
  3: 'grid-cols-1 md:grid-cols-3',
};

export function FormGrid({ children, columns = 1, className }: FormGridProps) {
  return (
    <div className={clsx('grid gap-nkz-stack', gridColumns[columns], className)}>
      {children}
    </div>
  );
}

/* ───────── FormField ───────── */

interface FormFieldProps {
  label: string;
  required?: boolean;
  description?: string;
  error?: string;
  span?: 1 | 2 | 3;
  children: React.ReactNode;
  className?: string;
}

const spanClasses: Record<NonNullable<FormFieldProps['span']>, string> = {
  1: 'col-span-1',
  2: 'col-span-1 md:col-span-2',
  3: 'col-span-1 md:col-span-3',
};

export function FormField({
  label,
  required,
  description,
  error,
  span = 1,
  children,
  className,
}: FormFieldProps) {
  return (
    <div className={clsx('flex flex-col gap-nkz-tight', spanClasses[span], className)}>
      <label className="flex items-center gap-nkz-tight text-nkz-sm font-medium text-nkz-text-primary">
        {label}
        {required && <span className="text-nkz-danger" aria-label="required">*</span>}
      </label>
      {description && (
        <p className="text-nkz-xs text-nkz-text-muted">{description}</p>
      )}
      {children}
      {error && (
        <p className="text-nkz-xs text-nkz-danger" role="alert">{error}</p>
      )}
    </div>
  );
}

/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center text-center py-nkz-section px-nkz-stack',
        className
      )}
    >
      {icon && (
        <div className="mb-nkz-stack text-nkz-text-muted">{icon}</div>
      )}
      <h3 className="text-nkz-md font-semibold text-nkz-text-primary mb-nkz-tight">
        {title}
      </h3>
      {description && (
        <p className="text-nkz-sm text-nkz-text-secondary max-w-sm mb-nkz-stack">
          {description}
        </p>
      )}
      {action && <div>{action}</div>}
    </div>
  );
}

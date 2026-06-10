/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';

/* ───────── SettingsList ───────── */

interface SettingsListProps {
  children: React.ReactNode;
  className?: string;
}

export function SettingsList({ children, className }: SettingsListProps) {
  return (
    <div className={clsx('divide-y divide-nkz-border', className)}>
      {children}
    </div>
  );
}

/* ───────── SettingsItem ───────── */

interface SettingsItemProps {
  label: string;
  description?: string;
  control?: React.ReactNode;
  className?: string;
}

export function SettingsItem({ label, description, control, className }: SettingsItemProps) {
  return (
    <div
      className={clsx(
        'flex items-center justify-between gap-nkz-stack py-nkz-inline',
        className
      )}
    >
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-nkz-sm font-medium text-nkz-text-primary truncate">
          {label}
        </span>
        {description && (
          <span className="text-nkz-xs text-nkz-text-muted">{description}</span>
        )}
      </div>
      {control && (
        <div className="flex-shrink-0">{control}</div>
      )}
    </div>
  );
}

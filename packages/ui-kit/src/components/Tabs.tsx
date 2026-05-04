/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import * as RadixTabs from '@radix-ui/react-tabs';
import clsx from 'clsx';

interface TabsProps {
  defaultValue: string;
  value?: string;
  onValueChange?: (value: string) => void;
  children: React.ReactNode;
}

interface TabsTriggerProps {
  value: string;
  children: React.ReactNode;
  count?: number;
  disabled?: boolean;
}

function TabsRoot({
  defaultValue,
  value,
  onValueChange,
  children,
}: TabsProps) {
  return (
    <RadixTabs.Root
      defaultValue={defaultValue}
      value={value}
      onValueChange={onValueChange}
    >
      {children}
    </RadixTabs.Root>
  );
}

function TabsList({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <RadixTabs.List className={clsx('flex border-b border-nkz-border', className)}>
      {children}
    </RadixTabs.List>
  );
}

function TabsTrigger({ value, children, count, disabled }: TabsTriggerProps) {
  return (
    <RadixTabs.Trigger
      value={value}
      disabled={disabled}
      className={clsx(
        'px-nkz-stack py-nkz-inline text-nkz-sm font-medium',
        'border-b-2 border-transparent -mb-[1px]',
        'text-nkz-text-secondary hover:text-nkz-text-primary',
        'transition-colors duration-nkz-fast',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'data-[state=active]:text-nkz-accent-base data-[state=active]:border-nkz-accent-base'
      )}
    >
      {children}
      {count !== undefined && (
        <span className="ml-nkz-tight text-nkz-xs text-nkz-text-muted">
          ({count})
        </span>
      )}
    </RadixTabs.Trigger>
  );
}

function TabsContent({
  value,
  children,
  className,
}: {
  value: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <RadixTabs.Content value={value} className={clsx('pt-nkz-stack', className)}>
      {children}
    </RadixTabs.Content>
  );
}

export const Tabs = Object.assign(TabsRoot, {
  List: TabsList,
  Trigger: TabsTrigger,
  Content: TabsContent,
});

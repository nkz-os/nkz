/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import clsx from 'clsx';
import { Surface } from './Surface';

interface PanelProps {
  variant?: 'glass' | 'solid';
  children: React.ReactNode;
  className?: string;
}

function PanelRoot({ variant = 'solid', children, className }: PanelProps) {
  return (
    <Surface
      variant="default"
      padding="none"
      radius="lg"
      className={clsx(
        variant === 'glass' && 'backdrop-blur-xl saturate-[180%]',
        'flex flex-col',
        className
      )}
    >
      {children}
    </Surface>
  );
}

function PanelHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={clsx(
        'flex items-center justify-between px-nkz-stack py-nkz-inline border-b border-nkz-border',
        className
      )}
    >
      {children}
    </div>
  );
}

function PanelTitle({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-nkz-inline text-nkz-text-primary text-nkz-md font-semibold">
      {icon && <span className="text-nkz-accent-base">{icon}</span>}
      {children}
    </div>
  );
}

function PanelActions({ children }: { children: React.ReactNode }) {
  return <div className="flex items-center gap-nkz-tight">{children}</div>;
}

function PanelBody({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={clsx('px-nkz-stack py-nkz-stack', className)}>{children}</div>;
}

function PanelFooter({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={clsx(
        'px-nkz-stack py-nkz-inline border-t border-nkz-border text-nkz-text-muted text-nkz-xs',
        className
      )}
    >
      {children}
    </div>
  );
}

export const Panel = Object.assign(PanelRoot, {
  Header: PanelHeader,
  Title: PanelTitle,
  Actions: PanelActions,
  Body: PanelBody,
  Footer: PanelFooter,
});

/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 */

import React from 'react';
import * as RadixDialog from '@radix-ui/react-dialog';
import { cn } from '../utils/cn';

/**
 * Modal — viewport-level dialog portaled to document.body.
 *
 * Why a portal: federated modules render inside host containers that may use
 * CSS containment or stacking isolation. A `position: fixed` overlay rendered
 * inline inside such a container is trapped by it — the container becomes its
 * containing block (the overlay is sized to the container box, not the
 * viewport) and paint containment clips whatever overflows it. Rendering
 * through a portal at document.body escapes both, so the modal is always
 * viewport-sized, centered and stacked above page chrome.
 *
 * Built on Radix Dialog: focus trap, ESC to close, click-outside to close,
 * scroll lock and ARIA wiring come for free.
 */

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

const SIZE_CLASSES: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
  full: 'max-w-[min(95vw,72rem)]',
};

interface ModalProps {
  /** Controlled open state. */
  open: boolean;
  /** Called with the next state (ESC, overlay click, ModalClose). */
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
  /** Width preset. Default 'md'. */
  size?: ModalSize;
  /** Render the default top-right close button. Default true. */
  showClose?: boolean;
  className?: string;
  overlayClassName?: string;
}

export function Modal({
  open,
  onOpenChange,
  children,
  size = 'md',
  showClose = true,
  className,
  overlayClassName,
}: ModalProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay
          className={cn(
            'fixed inset-0 z-[9998] bg-black/50 backdrop-blur-sm',
            overlayClassName
          )}
        />
        <RadixDialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-[9999]',
            'w-[calc(100vw-2rem)]',
            SIZE_CLASSES[size],
            'max-h-[calc(100vh-4rem)] overflow-y-auto',
            'bg-nkz-surface border border-nkz-border rounded-nkz-lg shadow-2xl',
            'focus:outline-none',
            className
          )}
        >
          {children}
          {showClose && (
            <RadixDialog.Close
              aria-label="Close"
              className={cn(
                'absolute right-nkz-inline top-nkz-inline',
                'inline-flex items-center justify-center w-8 h-8 rounded-nkz-md',
                'text-nkz-text-muted hover:text-nkz-text-primary hover:bg-nkz-surface-sunken',
                'transition-colors duration-nkz-fast',
                'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base'
              )}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </RadixDialog.Close>
          )}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

function ModalHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'flex items-start justify-between gap-nkz-inline',
        'px-nkz-stack pt-nkz-stack pb-nkz-inline',
        'border-b border-nkz-border',
        className
      )}
    >
      {children}
    </div>
  );
}

function ModalTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <RadixDialog.Title
      className={cn('text-nkz-md font-semibold text-nkz-text-primary', className)}
    >
      {children}
    </RadixDialog.Title>
  );
}

function ModalDescription({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <RadixDialog.Description
      className={cn('text-nkz-sm text-nkz-text-secondary mt-nkz-tight', className)}
    >
      {children}
    </RadixDialog.Description>
  );
}

function ModalBody({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('px-nkz-stack py-nkz-stack', className)}>{children}</div>;
}

function ModalFooter({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'flex items-center justify-end gap-nkz-inline',
        'px-nkz-stack py-nkz-inline',
        'border-t border-nkz-border',
        className
      )}
    >
      {children}
    </div>
  );
}

interface ModalCloseProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children: React.ReactNode;
}

function ModalClose({ children, className, ...props }: ModalCloseProps) {
  return (
    <RadixDialog.Close
      className={cn(
        'inline-flex items-center justify-center rounded-nkz-md',
        'transition-colors duration-nkz-fast',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-nkz-accent-base',
        className
      )}
      {...props}
    >
      {children}
    </RadixDialog.Close>
  );
}

export { ModalHeader, ModalTitle, ModalDescription, ModalBody, ModalFooter, ModalClose };

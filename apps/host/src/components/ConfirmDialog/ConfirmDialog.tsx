// =============================================================================
// ConfirmDialog - Accessible confirm dialog (design-system replacement for
// window.confirm)
// =============================================================================
// Rendered once by ConfirmProvider (see @/context/ConfirmContext). Not meant
// to be mounted directly by feature components — use the useConfirm() hook.

import React, { useEffect, useRef } from 'react';
import { Button } from '@nekazari/ui-kit';

export interface ConfirmDialogOptions {
  /** Optional heading. Omit for a single-sentence message-only dialog. */
  title?: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  /** 'danger' styles the confirm button for destructive actions. */
  tone?: 'default' | 'danger';
}

export interface ConfirmDialogProps extends ConfirmDialogOptions {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  tone = 'default',
  onConfirm,
  onCancel,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);

  // Esc cancels, Enter confirms (regardless of which button has focus —
  // mirrors native confirm()), Tab is trapped between the two buttons.
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        onConfirm();
        return;
      }
      if (e.key === 'Tab' && containerRef.current) {
        const focusables = containerRef.current.querySelectorAll<HTMLButtonElement>('button');
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [open, onConfirm, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-[9999]"
      onClick={onCancel}
    >
      <div
        ref={containerRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={title ? 'nkz-confirm-title' : undefined}
        aria-describedby="nkz-confirm-message"
        className="bg-nkz-surface rounded-lg p-6 max-w-md w-full mx-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <h3 id="nkz-confirm-title" className="text-nkz-lg font-bold text-nkz-text-primary">
            {title}
          </h3>
        )}
        <p
          id="nkz-confirm-message"
          className={`text-sm text-nkz-text-secondary ${title ? 'mt-2' : ''}`}
        >
          {message}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <Button
            variant="secondary"
            onClick={onCancel}
            autoFocus
          >
            {cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
};

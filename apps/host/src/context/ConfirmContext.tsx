// =============================================================================
// Confirm Context - Global Confirm Dialog Provider
// =============================================================================
// Design-system replacement for window.confirm(). Mirrors ToastContext: a
// single dialog instance lives at the app root, and useConfirm() gives any
// component (or plain hook — e.g. useUserActions) an imperative, awaitable
// confirm(options) function instead of requiring each caller to own dialog
// state and mount its own modal JSX.

import React, { createContext, useCallback, useContext, useRef, useState, ReactNode } from 'react';
import { ConfirmDialog, ConfirmDialogOptions } from '@/components/ConfirmDialog/ConfirmDialog';

type ConfirmFn = (options: ConfirmDialogOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | undefined>(undefined);

interface PendingConfirm extends ConfirmDialogOptions {
  resolve: (value: boolean) => void;
}

export const ConfirmProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    return new Promise<boolean>((resolve) => {
      setPending({ ...options, resolve });
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    setPending((current) => {
      current?.resolve(value);
      return null;
    });
    previouslyFocused.current?.focus();
    previouslyFocused.current = null;
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmDialog
        open={pending !== null}
        title={pending?.title}
        message={pending?.message ?? ''}
        confirmLabel={pending?.confirmLabel ?? ''}
        cancelLabel={pending?.cancelLabel ?? ''}
        tone={pending?.tone}
        onConfirm={() => settle(true)}
        onCancel={() => settle(false)}
      />
    </ConfirmContext.Provider>
  );
};

/** Returns an awaitable confirm(options) function. Must be called within ConfirmProvider. */
export const useConfirm = (): ConfirmFn => {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error('useConfirm must be used within ConfirmProvider');
  }
  return context;
};

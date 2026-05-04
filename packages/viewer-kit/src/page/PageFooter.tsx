/**
 * PageFooter — page footer with optional sticky behavior.
 */
import React from 'react';
import clsx from 'clsx';

export interface PageFooterProps {
  sticky?: boolean;
  children: React.ReactNode;
  className?: string;
}

function PageFooter({
  sticky = false,
  children,
  className,
}: PageFooterProps) {
  return (
    <footer
      className={clsx(
        'border-t border-nkz-border bg-nkz-surface px-nkz-section py-nkz-stack',
        sticky && 'sticky bottom-4',
        className,
      )}
    >
      {children}
    </footer>
  );
}

export { PageFooter };

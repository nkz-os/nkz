/**
 * PageSection — flat and card section layout components.
 *
 * PageSection:      Flat section with optional header (title, description, toolbar).
 * PageSection.Card: Card variant with border, shadow, and optional collapsible body.
 */
import React, { useState } from 'react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// PageSection (flat)
// ---------------------------------------------------------------------------

export interface PageSectionProps {
  title?: string;
  description?: string;
  toolbar?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

function PageSection({
  title,
  description,
  toolbar,
  children,
  className,
}: PageSectionProps) {
  return (
    <section className={clsx('py-nkz-stack', className)}>
      {(title || toolbar) && (
        <div className="flex items-start justify-between mb-nkz-inline">
          <div>
            {title && (
              <h2 className="text-nkz-md font-semibold text-nkz-text-primary">
                {title}
              </h2>
            )}
            {description && (
              <p className="text-nkz-sm text-nkz-text-muted mt-nkz-tight">
                {description}
              </p>
            )}
          </div>
          {toolbar && (
            <div className="flex items-center gap-nkz-tight flex-shrink-0 ml-nkz-stack">
              {toolbar}
            </div>
          )}
        </div>
      )}
      {children}
    </section>
  );
}

// ---------------------------------------------------------------------------
// PageSection.Card
// ---------------------------------------------------------------------------

export interface PageSectionCardProps {
  title?: string;
  description?: string;
  toolbar?: React.ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
  children: React.ReactNode;
  className?: string;
}

function PageSectionCard({
  title,
  description,
  toolbar,
  collapsible = false,
  defaultCollapsed = false,
  children,
  className,
}: PageSectionCardProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  return (
    <section
      className={clsx(
        'border border-nkz-border rounded-nkz-lg bg-nkz-surface shadow-nkz-sm',
        className,
      )}
    >
      {(title || toolbar || collapsible) && (
        <div
          className={clsx(
            'flex items-start justify-between px-nkz-stack py-nkz-inline',
            collapsible && 'cursor-pointer select-none',
            collapsed ? 'border-b-0' : 'border-b border-nkz-border',
          )}
          onClick={collapsible ? () => setCollapsed((c) => !c) : undefined}
        >
          <div className="flex items-center gap-nkz-inline min-w-0">
            {collapsible && (
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                fill="none"
                className={clsx(
                  'transition-transform duration-nkz-fast flex-shrink-0',
                  collapsed && '-rotate-90',
                )}
                aria-hidden="true"
              >
                <path
                  d="M3 4l2 2 2-2"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
            <div className="min-w-0">
              {title && (
                <h2 className="text-nkz-md font-semibold text-nkz-text-primary truncate">
                  {title}
                </h2>
              )}
              {description && (
                <p className="text-nkz-sm text-nkz-text-muted mt-nkz-tight">
                  {description}
                </p>
              )}
            </div>
          </div>
          {toolbar && (
            <div className="flex items-center gap-nkz-tight flex-shrink-0 ml-nkz-stack">
              {toolbar}
            </div>
          )}
        </div>
      )}
      {!collapsed && <div className="px-nkz-stack py-nkz-stack">{children}</div>}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Compound export
// ---------------------------------------------------------------------------

PageSection.Card = PageSectionCard;

export { PageSection };

/**
 * PageHeader — sticky page header with breadcrumbs, title row, description,
 * meta, and actions. Height is controlled by `--nkz-page-header-h` CSS var
 * (set by useScrollHeaderState).
 */
import React from 'react';
import clsx from 'clsx';

export interface Breadcrumb {
  label: string;
  href: string;
}

export interface PageHeaderProps {
  title: string;
  icon?: React.ReactNode;
  description?: string;
  breadcrumbs?: Breadcrumb[];
  status?: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}

function PageHeader({
  title,
  icon,
  description,
  breadcrumbs,
  status,
  meta,
  actions,
}: PageHeaderProps) {
  return (
    <div
      className={clsx(
        'sticky top-0 z-nkz-header',
        'bg-nkz-surface border-b border-nkz-border',
        'transition-[height] duration-nkz-normal',
        'overflow-hidden',
      )}
      style={{ height: 'var(--nkz-page-header-h, 96px)' }}
    >
      <div className="flex flex-col h-full px-nkz-section py-nkz-inline">
        {/* ---------- Breadcrumbs ---------- */}
        {breadcrumbs && breadcrumbs.length > 0 && (
          <nav className="flex items-center gap-nkz-tight text-nkz-xs text-nkz-text-muted mb-nkz-tight">
            {breadcrumbs.map((crumb, i) => (
              <React.Fragment key={crumb.href}>
                {i > 0 && (
                  <span className="text-nkz-text-muted" aria-hidden="true">
                    ▸
                  </span>
                )}
                <a
                  href={crumb.href}
                  className="hover:text-nkz-text-primary transition-colors duration-nkz-fast"
                >
                  {crumb.label}
                </a>
              </React.Fragment>
            ))}
          </nav>
        )}

        {/* ---------- Title row ---------- */}
        <div className="flex items-center gap-nkz-inline flex-1 min-w-0">
          {icon && (
            <span className="flex-shrink-0 text-nkz-accent-base">
              {icon}
            </span>
          )}
          <h1 className="text-nkz-xl font-bold text-nkz-text-primary truncate flex-1">
            {title}
          </h1>
          {status && <div className="flex-shrink-0">{status}</div>}
          {actions && (
            <div className="flex items-center gap-nkz-tight flex-shrink-0">
              {actions}
            </div>
          )}
        </div>

        {/* ---------- Description & meta ---------- */}
        {(description || meta) && (
          <div className="flex items-center gap-nkz-stack mt-nkz-tight">
            {description && (
              <p className="text-nkz-sm text-nkz-text-secondary truncate flex-1">
                {description}
              </p>
            )}
            {meta && (
              <div className="flex-shrink-0 text-nkz-xs text-nkz-text-muted">
                {meta}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export { PageHeader };

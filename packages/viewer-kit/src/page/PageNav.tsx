/**
 * PageNav — sidebar navigation compound component.
 *
 * Sub-components:
 *   PageNav.Item    — navigation link with active detection
 *   PageNav.Group   — labeled group
 *   PageNav.Divider — hairline separator
 */
import React from 'react';
import clsx from 'clsx';

// ---------------------------------------------------------------------------
// PageNav.Item
// ---------------------------------------------------------------------------

interface PageNavItemProps {
  to: string;
  exact?: boolean;
  icon?: React.ReactNode;
  count?: number;
  disabled?: boolean;
  children: React.ReactNode;
}

function PageNavItem({
  to,
  exact = false,
  icon,
  count,
  disabled = false,
  children,
}: PageNavItemProps) {
  const isActive = exact
    ? window.location.pathname === to
    : window.location.pathname.startsWith(to);

  return (
    <a
      href={disabled ? undefined : to}
      onClick={disabled ? (e) => e.preventDefault() : undefined}
      className={clsx(
        'flex items-center gap-nkz-inline px-nkz-inline py-nkz-tight rounded-nkz-sm',
        'text-nkz-sm transition-colors duration-nkz-fast',
        'relative',
        disabled &&
          'opacity-40 cursor-not-allowed pointer-events-none',
        isActive
          ? [
              'text-nkz-accent-strong font-medium',
              'bg-nkz-accent-soft',
            ]
          : 'text-nkz-text-secondary hover:text-nkz-text-primary hover:bg-nkz-surface-sunken',
      )}
    >
      {/* Active indicator — left border strip */}
      {isActive && (
        <span
          className="absolute left-0 top-1 bottom-1 w-0.5 rounded-r-full"
          style={{
            backgroundColor: 'var(--nkz-color-accent-base)',
          }}
          aria-hidden="true"
        />
      )}

      {/* Icon */}
      {icon && (
        <span className="flex-shrink-0 w-4 h-4 flex items-center justify-center">
          {icon}
        </span>
      )}

      {/* Label */}
      <span className="flex-1 truncate">{children}</span>

      {/* Count */}
      {count !== undefined && (
        <span className="text-nkz-2xs text-nkz-text-muted tabular-nums">
          {count}
        </span>
      )}
    </a>
  );
}

// ---------------------------------------------------------------------------
// PageNav.Group
// ---------------------------------------------------------------------------

interface PageNavGroupProps {
  label: string;
  children: React.ReactNode;
}

function PageNavGroup({ label, children }: PageNavGroupProps) {
  return (
    <div className="mb-nkz-stack">
      <p className="px-nkz-inline py-nkz-tight text-nkz-2xs font-semibold uppercase tracking-wide text-nkz-text-muted">
        {label}
      </p>
      <div className="flex flex-col gap-nkz-tight">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PageNav.Divider
// ---------------------------------------------------------------------------

function PageNavDivider() {
  return (
    <hr className="border-t border-nkz-border my-nkz-inline mx-nkz-inline" />
  );
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const PageNav = {
  Item: PageNavItem,
  Group: PageNavGroup,
  Divider: PageNavDivider,
};

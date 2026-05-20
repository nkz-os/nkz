/**
 * LayerMenuRow — reusable row for the Capas layer-toggle slot.
 *
 * Renders: icon + title + on/off switch, scope segmented control
 * (selected | all), an optional mode slot, and an optional opacity slider.
 *
 * The toggle is a native <input role="switch"> (not the ui-kit Toggle) so that
 * ARIA semantics are correct for assistive technologies and predictable in tests.
 */
import React, { ReactNode } from 'react';
import { SlotShellCompact } from '@nekazari/viewer-kit';

export type LayerScope = 'selected' | 'all';

export interface LayerMenuRowProps {
  moduleId: string;
  accent: { base: string; soft: string; strong: string };
  icon?: ReactNode;
  title: string;
  enabled: boolean;
  onToggle: (next: boolean) => void;
  scope: LayerScope;
  onScopeChange: (next: LayerScope) => void;
  /** When set, the toggle is disabled and this text is shown as the reason. */
  disabledReason?: string;
  /** Optional secondary control row — index selector, color mode picker, etc. */
  mode?: ReactNode;
  /** Optional 0–100 opacity slider. Both opacity and onOpacityChange must be set to render. */
  opacity?: number;
  onOpacityChange?: (next: number) => void;
}

const ScopeButton: React.FC<{
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}> = ({ active, onClick, children }) => (
  <button
    type="button"
    aria-pressed={active}
    onClick={onClick}
    className={`px-nkz-inline py-nkz-tight text-nkz-xs rounded-nkz-md transition-colors ${
      active
        ? 'bg-nkz-accent-soft text-nkz-accent-strong'
        : 'bg-nkz-surface-sunken text-nkz-text-muted hover:bg-nkz-surface'
    }`}
  >
    {children}
  </button>
);

export const LayerMenuRow: React.FC<LayerMenuRowProps> = ({
  moduleId,
  accent,
  icon,
  title,
  enabled,
  onToggle,
  scope,
  onScopeChange,
  disabledReason,
  mode,
  opacity,
  onOpacityChange,
}) => {
  const isDisabled = !!disabledReason;
  const showOpacity =
    enabled && typeof opacity === 'number' && typeof onOpacityChange === 'function';

  return (
    <SlotShellCompact moduleId={moduleId} accent={accent}>
      <div className="flex flex-col gap-nkz-tight">
        {/* Header row: icon + title + toggle */}
        <div className="flex items-center gap-nkz-inline">
          {icon && (
            <span className="text-nkz-accent-base flex-shrink-0">{icon}</span>
          )}
          <span className="flex-1 text-nkz-sm font-medium text-nkz-text">
            {title}
          </span>
          <input
            type="checkbox"
            role="switch"
            checked={enabled}
            disabled={isDisabled}
            onChange={e => onToggle(e.target.checked)}
            className="cursor-pointer accent-nkz-accent-base disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label={title}
          />
        </div>

        {/* Reason line for disabled state */}
        {isDisabled && (
          <span className="text-nkz-xs text-nkz-text-muted">{disabledReason}</span>
        )}

        {/* Scope segmented control */}
        <div className="flex items-center gap-nkz-tight">
          <span className="text-nkz-xs text-nkz-text-muted flex-shrink-0">Scope</span>
          <ScopeButton
            active={scope === 'selected'}
            onClick={() => onScopeChange('selected')}
          >
            Selected
          </ScopeButton>
          <ScopeButton active={scope === 'all'} onClick={() => onScopeChange('all')}>
            All
          </ScopeButton>
        </div>

        {/* Optional mode slot (index selector, color mode picker, etc.) */}
        {mode && (
          <div className="flex items-center gap-nkz-tight">{mode}</div>
        )}

        {/* Optional opacity slider — only rendered when layer is enabled */}
        {showOpacity && (
          <div className="flex items-center gap-nkz-tight">
            <span className="text-nkz-xs text-nkz-text-muted flex-shrink-0">
              Opacity
            </span>
            <input
              type="range"
              min={0}
              max={100}
              value={opacity}
              onChange={e => onOpacityChange!(Number(e.target.value))}
              className="flex-1 accent-nkz-accent-base"
            />
            <span className="text-nkz-xs text-nkz-text-muted w-8 text-right">
              {opacity}%
            </span>
          </div>
        )}
      </div>
    </SlotShellCompact>
  );
};

export default LayerMenuRow;

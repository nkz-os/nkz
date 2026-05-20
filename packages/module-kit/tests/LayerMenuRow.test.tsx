import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

afterEach(cleanup);
import { LayerMenuRow } from '../src/components/LayerMenuRow';

describe('LayerMenuRow', () => {
  const baseProps = {
    moduleId: 'test-mod',
    accent: { base: '#65A30D', soft: '#ECFCCB', strong: '#4D7C0F' },
    title: 'Vegetation',
    enabled: false,
    onToggle: vi.fn(),
    scope: 'selected' as const,
    onScopeChange: vi.fn(),
  };

  it('renders the title and toggle', () => {
    render(<LayerMenuRow {...baseProps} />);
    expect(screen.getByText('Vegetation')).toBeInTheDocument();
    expect(screen.getByRole('switch')).not.toBeChecked();
  });

  it('calls onToggle when the switch is clicked', () => {
    const onToggle = vi.fn();
    render(<LayerMenuRow {...baseProps} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('renders both scope options and marks the active one', () => {
    render(<LayerMenuRow {...baseProps} scope="all" />);
    const allBtn = screen.getByRole('button', { name: /^all$/i });
    const selBtn = screen.getByRole('button', { name: /^selected$/i });
    expect(allBtn).toHaveAttribute('aria-pressed', 'true');
    expect(selBtn).toHaveAttribute('aria-pressed', 'false');
  });

  it('calls onScopeChange when the inactive scope is clicked', () => {
    const onScopeChange = vi.fn();
    render(<LayerMenuRow {...baseProps} scope="selected" onScopeChange={onScopeChange} />);
    fireEvent.click(screen.getByRole('button', { name: /^all$/i }));
    expect(onScopeChange).toHaveBeenCalledWith('all');
  });

  it('disables the toggle and shows the reason when disabledReason is provided', () => {
    render(<LayerMenuRow {...baseProps} disabledReason="No data" />);
    expect(screen.getByRole('switch')).toBeDisabled();
    expect(screen.getByText('No data')).toBeInTheDocument();
  });

  it('renders the optional mode and opacity slots', () => {
    render(
      <LayerMenuRow
        {...baseProps}
        enabled={true}
        mode={<div data-testid="mode-content">NDVI</div>}
        opacity={75}
        onOpacityChange={vi.fn()}
      />
    );
    expect(screen.getByTestId('mode-content')).toBeInTheDocument();
    expect(screen.getByRole('slider')).toHaveValue('75');
  });

  it('does not render opacity slider when enabled is false', () => {
    render(<LayerMenuRow {...baseProps} enabled={false} opacity={75} onOpacityChange={vi.fn()} />);
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
  });

  it('uses custom labels when provided', () => {
    render(
      <LayerMenuRow
        {...baseProps}
        scopeLabel="Ámbito"
        selectedLabel="Seleccionada"
        allLabel="Todas"
        opacityLabel="Opacidad"
        enabled={true}
        opacity={50}
        onOpacityChange={vi.fn()}
      />
    );
    expect(screen.getByText('Ámbito')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^seleccionada$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^todas$/i })).toBeInTheDocument();
    expect(screen.getByRole('slider')).toHaveAttribute('aria-label', 'Opacidad');
  });
});

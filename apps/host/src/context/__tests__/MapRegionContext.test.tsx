import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MapRegionProvider, useMapRegion } from '../MapRegionContext';

function Probe() {
  const { currentRegion, layerAutoMode, setManual, setRegion, enableAuto } = useMapRegion();
  return (
    <div>
      <span data-testid="r">{currentRegion}</span>
      <span data-testid="auto">{String(layerAutoMode)}</span>
      <button onClick={() => setRegion('spain')}>region</button>
      <button onClick={() => setManual()}>manual</button>
      <button onClick={() => enableAuto()}>auto</button>
    </div>
  );
}

describe('MapRegionContext', () => {
  it('defaults to eu + auto', () => {
    render(<MapRegionProvider><Probe /></MapRegionProvider>);
    expect(screen.getByTestId('r').textContent).toBe('eu');
    expect(screen.getByTestId('auto').textContent).toBe('true');
  });

  it('setRegion updates currentRegion', () => {
    render(<MapRegionProvider><Probe /></MapRegionProvider>);
    act(() => screen.getByText('region').click());
    expect(screen.getByTestId('r').textContent).toBe('spain');
  });

  it('setManual disables layerAutoMode', () => {
    render(<MapRegionProvider><Probe /></MapRegionProvider>);
    act(() => screen.getByText('manual').click());
    expect(screen.getByTestId('auto').textContent).toBe('false');
  });

  it('enableAuto re-enables after manual', () => {
    render(<MapRegionProvider><Probe /></MapRegionProvider>);
    act(() => screen.getByText('manual').click());
    expect(screen.getByTestId('auto').textContent).toBe('false');
    act(() => screen.getByText('auto').click());
    expect(screen.getByTestId('auto').textContent).toBe('true');
  });

  it('useMapRegion throws outside provider', () => {
    // Suppress console.error for the expected React error boundary
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow('useMapRegion must be used within MapRegionProvider');
    consoleSpy.mockRestore();
  });
});

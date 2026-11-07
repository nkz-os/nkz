import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MapSearchLupa } from '../MapSearchLupa';

const mockUseGeocoder = vi.fn();
vi.mock('@/hooks/useGeocoder', () => ({
  useGeocoder: (...args: any[]) => mockUseGeocoder(...args),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d || _k, i18n: { language: 'es' } }),
}));

const baseResult = {
  label: 'Pamplona', name: 'Pamplona', secondary: 'Navarra, España',
  lat: 42.8, lon: -1.6, bbox: null, type: 'city', countryCode: 'ES',
};

function mockHook(overrides: Record<string, unknown> = {}) {
  mockUseGeocoder.mockReturnValue({
    results: [], loading: false, error: null,
    search: vi.fn(), clear: vi.fn(),
    ...overrides,
  });
}

describe('MapSearchLupa', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHook();
  });

  it('starts collapsed, expands on click, picks a result', () => {
    mockHook({ results: [baseResult] });
    const onPick = vi.fn();
    render(<MapSearchLupa onPick={onPick} />);

    expect(screen.queryByRole('textbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Pam' } });
    fireEvent.click(screen.getByText('Pamplona'));
    expect(onPick).toHaveBeenCalledWith(baseResult);
    // Picking closes and clears
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('shows no results message when query has no matches', () => {
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'zzz' } });
    expect(screen.getByText('map.search_no_results')).toBeInTheDocument();
  });

  it('shows loading spinner', () => {
    mockHook({ loading: true });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(document.querySelector('.animate-spin')).not.toBeNull();
  });

  it('shows error message', () => {
    mockHook({ error: 'Boom' });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByText('map.search_error')).toBeInTheDocument();
  });

  it('Enter picks the first result when none is highlighted', () => {
    const onPick = vi.fn();
    mockHook({ results: [baseResult] });
    render(<MapSearchLupa onPick={onPick} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(baseResult);
  });

  it('ArrowDown highlights and Enter picks the highlighted result', () => {
    const onPick = vi.fn();
    const other = { ...baseResult, name: 'Pampliega', label: 'Pampliega' };
    mockHook({ results: [baseResult, other] });
    render(<MapSearchLupa onPick={onPick} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'ArrowDown' });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(baseResult);
  });

  it('closes on Escape key and clears through the hook', () => {
    const clear = vi.fn();
    mockHook({ results: [baseResult], clear });
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(clear).toHaveBeenCalled();
  });

  it('notifies active state transitions', () => {
    const onActiveChange = vi.fn();
    render(<MapSearchLupa onPick={vi.fn()} onActiveChange={onActiveChange} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(onActiveChange).toHaveBeenLastCalledWith(true);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
    expect(onActiveChange).toHaveBeenLastCalledWith(false);
  });

  it('does not fire onActiveChange loops for unstable parent callbacks', () => {
    let calls = 0;
    const Unstable = () => {
      calls += 1;
      // New inline identity every render — the ref must absorb it
      return <MapSearchLupa onPick={vi.fn()} onActiveChange={() => { /* noop */ }} />;
    };
    render(<Unstable />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(calls).toBeLessThan(5); // no render loop
  });

  it('closes on click outside when the query is empty', () => {
    render(<MapSearchLupa onPick={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /search/i }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TimelineMarkers, type TimelineMarker } from '../TimelineMarkers';
import { DAY_MS, timeToPct } from '../timeScale';

const range = { start: 0, end: 100 * DAY_MS };
const at = (day: number) => day * DAY_MS;

// Shared vertical mapping from the spec: PAD = 12 so end markers are not clipped.
const topPct = (y: number) => 12 + (1 - y) * (100 - 2 * 12);

const m = (id: string, day: number, extra: Partial<TimelineMarker> = {}): TimelineMarker => ({
  id, time: at(day), color: '#22c55e', title: id, ...extra,
});

const button = (name: string) => screen.getByRole('button', { name });

describe('TimelineMarkers vertical position', () => {
  it('keeps the dot centred when no y is given (backwards compatible)', () => {
    render(<TimelineMarkers range={range} markers={[m('a', 10)]} />);
    expect(button('a').style.top).toBe('50%');
  });

  it('maps y = 0, 1 and 0.5 to 88%, 12% and 50% from the top', () => {
    render(
      <TimelineMarkers
        range={range}
        markers={[m('low', 10, { y: 0 }), m('high', 20, { y: 1 }), m('mid', 30, { y: 0.5 })]}
      />,
    );
    expect(button('low').style.top).toBe('88%');
    expect(button('high').style.top).toBe('12%');
    expect(button('mid').style.top).toBe('50%');
  });

  it('clamps y outside [0, 1]', () => {
    render(<TimelineMarkers range={range} markers={[m('below', 10, { y: -3 }), m('above', 20, { y: 7 })]} />);
    expect(button('below').style.top).toBe('88%');
    expect(button('above').style.top).toBe('12%');
  });
});

describe('TimelineMarkers connecting line', () => {
  const parsePoints = (container: HTMLElement) => {
    const poly = container.querySelector('polyline');
    expect(poly).not.toBeNull();
    return poly!.getAttribute('points')!.trim().split(/\s+/);
  };

  it('draws one polyline through in-range markers that have y, sorted by time', () => {
    const { container } = render(
      <TimelineMarkers
        connect
        range={range}
        markers={[
          m('c', 80, { y: 1 }),
          m('a', 20, { y: 0 }),
          m('no-y', 50),
          m('outside', 200, { y: 0.5 }),
          m('b', 40, { y: 0.5 }),
        ]}
      />,
    );
    expect(container.querySelectorAll('polyline')).toHaveLength(1);
    expect(parsePoints(container)).toEqual([
      `${timeToPct(at(20), range)},${topPct(0)}`,
      `${timeToPct(at(40), range)},${topPct(0.5)}`,
      `${timeToPct(at(80), range)},${topPct(1)}`,
    ]);
  });

  it('uses the same vertical mapping as the dots, clamped', () => {
    const { container } = render(
      <TimelineMarkers connect range={range} markers={[m('a', 10, { y: -2 }), m('b', 60, { y: 9 })]} />,
    );
    expect(parsePoints(container)).toEqual([
      `${timeToPct(at(10), range)},${topPct(0)}`,
      `${timeToPct(at(60), range)},${topPct(1)}`,
    ]);
    expect(button('a').style.top).toBe(`${topPct(0)}%`);
    expect(button('b').style.top).toBe(`${topPct(1)}%`);
  });

  it('draws a non-scaling thin stroke with the default muted colour', () => {
    const { container } = render(
      <TimelineMarkers connect range={range} markers={[m('a', 10, { y: 0 }), m('b', 20, { y: 1 })]} />,
    );
    const poly = container.querySelector('polyline')!;
    expect(poly.getAttribute('vector-effect')).toBe('non-scaling-stroke');
    expect(poly.getAttribute('stroke')).toBe('var(--nkz-color-text-muted, #94a3b8)');
    expect(poly.getAttribute('stroke-width')).toBe('1.5');
    expect(poly.getAttribute('fill')).toBe('none');
  });

  it('honours lineColor', () => {
    const { container } = render(
      <TimelineMarkers connect lineColor="#ff0000" range={range} markers={[m('a', 10, { y: 0 }), m('b', 20, { y: 1 })]} />,
    );
    expect(container.querySelector('polyline')!.getAttribute('stroke')).toBe('#ff0000');
  });

  it('is rendered behind the dots and never intercepts clicks', () => {
    const { container } = render(
      <TimelineMarkers connect range={range} markers={[m('a', 10, { y: 0 }), m('b', 20, { y: 1 })]} />,
    );
    const svg = container.querySelector('svg')!;
    expect(svg.compareDocumentPosition(button('a')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(svg.style.pointerEvents).toBe('none');
    expect(svg.getAttribute('aria-hidden')).toBe('true');
  });

  it('draws no line without connect', () => {
    const { container } = render(
      <TimelineMarkers range={range} markers={[m('a', 10, { y: 0 }), m('b', 20, { y: 1 })]} />,
    );
    expect(container.querySelector('polyline')).toBeNull();
    expect(container.querySelector('svg')).toBeNull();
  });

  it('draws no line with fewer than two plottable markers', () => {
    const { container } = render(
      <TimelineMarkers connect range={range} markers={[m('a', 10, { y: 0 }), m('no-y', 20)]} />,
    );
    expect(container.querySelector('polyline')).toBeNull();
  });
});

describe('TimelineMarkers hit area and dot size', () => {
  it('uses a 24x24 transparent button around a 10px dot', () => {
    render(<TimelineMarkers range={range} markers={[m('a', 10, { y: 0.5 })]} />);
    const btn = button('a');
    expect(btn.style.width).toBe('24px');
    expect(btn.style.height).toBe('24px');
    expect(btn.style.backgroundColor).toBe('transparent');
    const dot = btn.querySelector('span') as HTMLElement;
    expect(dot.style.width).toBe('10px');
    expect(dot.style.height).toBe('10px');
    expect(dot.style.backgroundColor).toBe('rgb(34, 197, 94)');
  });

  it('grows the dot to 14px when selected and keeps the button at 24x24', () => {
    render(<TimelineMarkers range={range} markers={[m('a', 10, { selected: true })]} />);
    const btn = button('a');
    expect(btn.style.width).toBe('24px');
    expect(btn.style.height).toBe('24px');
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    const dot = btn.querySelector('span') as HTMLElement;
    expect(dot.style.width).toBe('14px');
    expect(dot.style.height).toBe('14px');
    expect(dot.style.boxShadow).not.toBe('none');
  });

  it('keeps title and aria-label', () => {
    render(<TimelineMarkers range={range} markers={[m('a', 10, { title: 'Sentinel 2026-01-10' })]} />);
    const btn = screen.getByRole('button', { name: 'Sentinel 2026-01-10' });
    expect(btn.getAttribute('title')).toBe('Sentinel 2026-01-10');
  });

  it('calls onSelect with the marker id when clicked', () => {
    const onSelect = vi.fn();
    render(<TimelineMarkers range={range} markers={[m('a', 10), m('b', 20)]} onSelect={onSelect} />);
    fireEvent.click(button('b'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('calls onSelect when the visible dot itself is clicked', () => {
    const onSelect = vi.fn();
    render(<TimelineMarkers range={range} markers={[m('a', 10)]} onSelect={onSelect} />);
    fireEvent.click(button('a').querySelector('span')!);
    expect(onSelect).toHaveBeenCalledWith('a');
  });

  it('still hides markers outside the range', () => {
    render(<TimelineMarkers range={range} markers={[m('in', 10), m('out', 500)]} />);
    expect(screen.queryByRole('button', { name: 'out' })).toBeNull();
    expect(button('in')).toBeTruthy();
  });
});

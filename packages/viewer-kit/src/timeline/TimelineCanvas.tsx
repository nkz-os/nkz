/**
 * Canvas-rendered time axis with cursor, "now" marker, forecast zone, and tick labels.
 * Fills available width with a fixed height (32px compact / 40px expanded).
 */
import React, { useRef, useEffect, useCallback, useState } from 'react';
import clsx from 'clsx';

interface TimelineCanvasProps {
  startTime: number;
  endTime: number;
  cursor: number;
  onCursorChange: (time: number) => void;
  forecastFrom?: number;
  snapping?: 'hour' | 'day' | 'week' | null;
  height?: number;
  className?: string;
}

function snapTime(time: number, snapping: 'hour' | 'day' | 'week' | null): number {
  const d = new Date(time);
  switch (snapping) {
    case 'hour':
      d.setMinutes(0, 0, 0);
      break;
    case 'day':
      d.setHours(0, 0, 0, 0);
      break;
    case 'week':
      d.setHours(0, 0, 0, 0);
      d.setDate(d.getDate() - d.getDay());
      break;
  }
  return d.getTime();
}

export function TimelineCanvas({
  startTime,
  endTime,
  cursor,
  onCursorChange,
  forecastFrom,
  snapping = null,
  height = 32,
  className,
}: TimelineCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [canvasWidth, setCanvasWidth] = useState(0);

  const timeToX = useCallback(
    (t: number, w: number) => ((t - startTime) / (endTime - startTime)) * w,
    [startTime, endTime]
  );

  const xToTime = useCallback(
    (x: number, w: number) => startTime + (x / w) * (endTime - startTime),
    [startTime, endTime]
  );

  // ResizeObserver to track container width
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setCanvasWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Render canvas whenever dimensions or props change
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || canvasWidth === 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = canvasWidth * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${canvasWidth}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const w = canvasWidth;
    const h = height;
    ctx.clearRect(0, 0, w, h);

    // Read CSS custom properties for canvas rendering
    const style = getComputedStyle(document.documentElement);
    const textMuted = style.getPropertyValue('--nkz-color-text-muted').trim() || '#94A3B8';
    const border = style.getPropertyValue('--nkz-color-border').trim() || 'rgba(148,163,184,0.14)';
    const accent = style.getPropertyValue('--nkz-color-accent-base').trim() || '#8B5CF6';
    const surfaceSunken =
      style.getPropertyValue('--nkz-color-surface-sunken').trim() || 'rgba(51,65,85,0.55)';

    const baseY = h - 8;

    // Forecast zone shading
    if (forecastFrom && forecastFrom > startTime && forecastFrom < endTime) {
      const fx = timeToX(forecastFrom, w);
      ctx.fillStyle = surfaceSunken;
      ctx.fillRect(fx, 0, w - fx, h);

      // Diagonal stripes
      ctx.save();
      ctx.beginPath();
      ctx.rect(fx, 0, w - fx, h);
      ctx.clip();
      ctx.strokeStyle = border;
      ctx.lineWidth = 1;
      for (let i = -h; i < w + h; i += 8) {
        ctx.beginPath();
        ctx.moveTo(fx + i, 0);
        ctx.lineTo(fx + i - h, h);
        ctx.stroke();
      }
      ctx.restore();
    }

    // Timeline baseline
    ctx.strokeStyle = border;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, baseY);
    ctx.lineTo(w, baseY);
    ctx.stroke();

    // Tick marks and labels
    const range = endTime - startTime;
    let tickInterval: number;
    if (range <= 24 * 3600 * 1000) tickInterval = 3600 * 1000;
    else if (range <= 7 * 24 * 3600 * 1000) tickInterval = 24 * 3600 * 1000;
    else if (range <= 60 * 24 * 3600 * 1000) tickInterval = 7 * 24 * 3600 * 1000;
    else tickInterval = 30 * 24 * 3600 * 1000;

    const firstTick = Math.ceil(startTime / tickInterval) * tickInterval;

    ctx.fillStyle = textMuted;
    ctx.font = '10px Inter, system-ui, sans-serif';
    ctx.textAlign = 'center';

    for (let t = firstTick; t < endTime; t += tickInterval) {
      const x = timeToX(t, w);
      if (x < 20 || x > w - 20) continue;

      ctx.beginPath();
      ctx.moveTo(x, baseY - 4);
      ctx.lineTo(x, baseY + 4);
      ctx.stroke();

      const d = new Date(t);
      const label =
        tickInterval < 24 * 3600 * 1000
          ? `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
          : `${d.getDate()}/${d.getMonth() + 1}`;
      ctx.fillText(label, x, baseY - 8);
    }

    // "Now" marker (dashed vertical line)
    const now = Date.now();
    if (now >= startTime && now <= endTime) {
      const nx = timeToX(now, w);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(nx, 0);
      ctx.lineTo(nx, h);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Cursor triangle and vertical line
    const cx = timeToX(cursor, w);
    ctx.fillStyle = accent;
    ctx.beginPath();
    ctx.moveTo(cx - 5, h - 14);
    ctx.lineTo(cx + 5, h - 14);
    ctx.lineTo(cx, h - 4);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(cx, 0);
    ctx.lineTo(cx, h - 14);
    ctx.stroke();
  }, [startTime, endTime, cursor, forecastFrom, height, canvasWidth, timeToX]);

  // Pointer interaction helpers
  const getX = useCallback(
    (e: React.MouseEvent | React.TouchEvent): number => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      const clientX = 'touches' in e ? e.touches[0].clientX : e.clientX;
      return clientX - rect.left;
    },
    []
  );

  const handlePointerDown = useCallback(
    (e: React.MouseEvent | React.TouchEvent) => {
      dragging.current = true;
      const x = getX(e);
      onCursorChange(snapTime(xToTime(x, canvasWidth), snapping));
    },
    [xToTime, snapping, onCursorChange, canvasWidth, getX]
  );

  const handlePointerMove = useCallback(
    (e: React.MouseEvent | React.TouchEvent) => {
      if (!dragging.current) return;
      const x = getX(e);
      onCursorChange(snapTime(xToTime(x, canvasWidth), snapping));
    },
    [xToTime, snapping, onCursorChange, canvasWidth, getX]
  );

  const handlePointerUp = useCallback(() => {
    dragging.current = false;
  }, []);

  return (
    <div
      ref={containerRef}
      className={clsx('relative w-full select-none', className)}
    >
      <canvas
        ref={canvasRef}
        className="w-full cursor-pointer touch-none"
        style={{ height }}
        onMouseDown={handlePointerDown}
        onMouseMove={handlePointerMove}
        onMouseUp={handlePointerUp}
        onMouseLeave={handlePointerUp}
        onTouchStart={handlePointerDown}
        onTouchMove={handlePointerMove}
        onTouchEnd={handlePointerUp}
      />
    </div>
  );
}

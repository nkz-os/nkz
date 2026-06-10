/**
 * CSS overlay for the forecast zone — rendered as a striped background
 * behind forecast tracks when the timeline is expanded.
 */
import React from 'react';

interface TimelineForecastZoneProps {
  forecastFrom: number;
  startTime: number;
  endTime: number;
  children?: React.ReactNode;
}

export function TimelineForecastZone({
  forecastFrom,
  startTime,
  endTime,
  children,
}: TimelineForecastZoneProps) {
  const range = endTime - startTime;
  const leftPercent = ((forecastFrom - startTime) / range) * 100;

  if (leftPercent >= 100 || leftPercent <= 0) return <>{children}</>;

  return (
    <div className="relative">
      {/* Forecast zone background */}
      <div
        className="absolute top-0 bottom-0 pointer-events-none"
        style={{
          left: `${leftPercent}%`,
          right: 0,
          background: `repeating-linear-gradient(
            -45deg,
            transparent,
            transparent 4px,
            var(--nkz-color-border, rgba(148,163,184,0.14)) 4px,
            var(--nkz-color-border, rgba(148,163,184,0.14)) 6px
          )`,
          backgroundColor: 'var(--nkz-color-surface-sunken, rgba(51,65,85,0.55))',
        }}
      />
      {children}
    </div>
  );
}

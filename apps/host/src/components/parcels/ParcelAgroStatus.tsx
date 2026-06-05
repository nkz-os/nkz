/**
 * ParcelAgroStatus - Component to display agronomic semaphores for a parcel
 * Uses lazy loading with IntersectionObserver for performance
 */

import React, { useState, useEffect, useRef } from 'react';
import { Wind, Droplets, Thermometer, Radio, Cloud } from 'lucide-react';
import api, { type AgroStatusResponse } from '@/services/api';
import { logger } from '@/utils/logger';
import { safeFixed } from '@/utils/format';


interface ParcelAgroStatusProps {
  parcelId: string;
  isVisible?: boolean; // For manual control, otherwise uses IntersectionObserver
}

const SemaphoreIcon: React.FC<{
  status: string;
  icon: React.ReactNode;
  label: string;
  tooltip?: string;
}> = ({ status, icon, label, tooltip }) => {
  const getColor = () => {
    switch (status) {
      case 'optimal':
      case 'satisfied':
        return 'text-nkz-success bg-nkz-success-light border-green-300';
      case 'caution':
      case 'alert':
        return 'text-nkz-warning bg-nkz-warning-light border-yellow-300';
      case 'not_suitable':
      case 'too_wet':
      case 'too_dry':
      case 'deficit':
        return 'text-nkz-error bg-nkz-error-light border-red-300';
      default:
        return 'text-nkz-muted bg-nkz-bg-secondary border-nkz-border';
    }
  };

  return (
    <div
      className={`flex items-center justify-center w-9 h-9 rounded-full border-2 ${getColor()} transition-all hover:scale-110 cursor-help`}
      title={tooltip || label}
    >
      {icon}
    </div>
  );
};

export const ParcelAgroStatus: React.FC<ParcelAgroStatusProps> = ({
  parcelId,
  isVisible: manualVisible,
}) => {
  const [status, setStatus] = useState<AgroStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // IntersectionObserver for lazy loading
  useEffect(() => {
    if (manualVisible !== undefined) {
      setIsVisible(manualVisible);
      return;
    }

    if (!ref.current) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setIsVisible(true);
            observer.disconnect();
          }
        });
      },
      {
        rootMargin: '50px', // Start loading 50px before visible
        threshold: 0.1,
      }
    );

    observer.observe(ref.current);

    return () => {
      observer.disconnect();
    };
  }, [manualVisible]);

  // Load status when visible
  useEffect(() => {
    if (!isVisible || status || loading) return;

    const loadStatus = async () => {
      setLoading(true);
      setError(null);

      try {
        // Call API endpoint
        const response = await api.getParcelAgroStatus(parcelId);
        setStatus(response);
      } catch (err: any) {
        logger.error('Error loading parcel agro status:', err);
        const errorMsg = err?.response?.data?.error || err?.response?.data?.details || err?.message || 'Error cargando estado agronómico';
        // Check if it's a location/geometry error
        if (errorMsg.toLowerCase().includes('location') || errorMsg.toLowerCase().includes('geometry')) {
          setError('Parcela sin ubicación');
        } else {
          setError(errorMsg);
        }
      } finally {
        setLoading(false);
      }
    };

    loadStatus();
  }, [parcelId, isVisible, status, loading]);

  if (!isVisible) {
    return (
      <div ref={ref} className="flex items-center gap-2 text-nkz-muted">
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary animate-pulse" />
      </div>
    );
  }

  if (error || !status) {
    const msg =
      error && error.toLowerCase().includes('location')
        ? 'Sin ubicación de parcela'
        : error || 'Sin datos';
    return (
      <div className="flex items-center gap-2 text-nkz-muted" title={msg}>
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary flex items-center justify-center">
          <span className="text-[10px] text-nkz-muted">—</span>
        </div>
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary flex items-center justify-center">
          <span className="text-[10px] text-nkz-muted">—</span>
        </div>
        <div className="w-8 h-8 rounded-full bg-nkz-bg-secondary flex items-center justify-center">
          <span className="text-[10px] text-nkz-muted">—</span>
        </div>
      </div>
    );
  }

  const { semaphores, source_confidence, metrics } = status;

  // Enhanced tooltip content
  const getSprayingTooltip = () => {
    const statusText = semaphores.spraying === 'optimal' 
      ? 'Óptimo para pulverizar' 
      : semaphores.spraying === 'caution' 
      ? 'Precaución: condiciones límite' 
      : 'No recomendado';
    const deltaT = metrics?.delta_t != null ? `\nΔT: ${safeFixed(metrics.delta_t, 1)}°C` : '';
    const wind = metrics?.wind_speed != null ? `\nViento: ${safeFixed(metrics.wind_speed * 3.6, 1)} km/h` : '';
    return `${statusText}${deltaT}${wind}`;
  };

  const getWorkabilityTooltip = () => {
    const statusText = semaphores.workability === 'optimal' 
      ? 'Suelo en buen tempero' 
      : semaphores.workability === 'too_wet' 
      ? 'Demasiado húmedo' 
      : semaphores.workability === 'too_dry' 
      ? 'Demasiado seco' 
      : 'Precaución';
    const humidity = metrics?.humidity != null ? `\nHumedad: ${safeFixed(metrics.humidity, 0)}%` : '';
    return `${statusText}${humidity}`;
  };

  const getIrrigationTooltip = () => {
    const statusText = semaphores.irrigation === 'satisfied' 
      ? 'Riego satisfecho' 
      : semaphores.irrigation === 'alert' 
      ? 'Alerta: vigilar' 
      : 'Déficit hídrico';
    const balance = metrics?.water_balance != null
      ? `\nBalance 3 días: ${metrics.water_balance > 0 ? '+' : ''}${safeFixed(metrics.water_balance)} mm`
      : '';
    return `${statusText}${balance}`;
  };

  return (
    <div className="flex items-center gap-1.5">
      {/* Spraying Semaphore */}
      <SemaphoreIcon
        status={semaphores.spraying}
        icon={<Wind className="w-4 h-4" />}
        label="Pulverización"
        tooltip={getSprayingTooltip()}
      />

      {/* Workability Semaphore */}
      <SemaphoreIcon
        status={semaphores.workability}
        icon={<Droplets className="w-4 h-4" />}
        label="Tempero"
        tooltip={getWorkabilityTooltip()}
      />

      {/* Irrigation Semaphore */}
      <SemaphoreIcon
        status={semaphores.irrigation}
        icon={<Thermometer className="w-4 h-4" />}
        label="Riego"
        tooltip={getIrrigationTooltip()}
      />

      {/* Source Confidence Indicator - More visible */}
      <div 
        className="ml-1 flex items-center" 
        title={source_confidence === 'SENSOR_REAL' ? 'Datos de sensor real' : 'Datos de modelo meteorológico (Open-Meteo)'}
      >
        {source_confidence === 'SENSOR_REAL' ? (
          <Radio className="w-3.5 h-3.5 text-nkz-info" />
        ) : (
          <Cloud className="w-3.5 h-3.5 text-nkz-muted" />
        )}
      </div>
    </div>
  );
};


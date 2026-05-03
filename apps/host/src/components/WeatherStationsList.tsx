// =============================================================================
// Weather Stations List — self-contained component for the weather page.
// Fetches WeatherObserved entities from Orion-LD and shows latest readings.
// =============================================================================

import React, { useState, useEffect } from 'react';
import { Cloud, Thermometer, Droplets, Wind, Loader2 } from 'lucide-react';
import api from '@/services/api';
import { logger } from '@/utils/logger';

interface WeatherStationReading {
  id: string;
  name: string;
  temperature?: number;
  humidity?: number;
  windSpeed?: number;
  lastObserved?: string;
}

export const WeatherStationsList: React.FC = () => {
  const [stations, setStations] = useState<WeatherStationReading[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const stationsData = await api.getWeatherStations();

        const results: WeatherStationReading[] = stationsData
          .map((ws: any) => ({
            id: ws.id || '',
            name: ws.name || ws.id?.split(':')?.pop() || 'Estación',
            temperature: ws.readings?.temperature
              ? parseFloat(ws.readings.temperature)
              : undefined,
            humidity: ws.readings?.humidity
              ? parseFloat(ws.readings.humidity)
              : undefined,
            windSpeed: ws.readings?.windSpeed
              ? parseFloat(ws.readings.windSpeed)
              : undefined,
            lastObserved: ws.readings?.observedAt
              ? new Date(ws.readings.observedAt).toISOString()
              : undefined,
          }))
          .filter((s: WeatherStationReading) =>
            s.temperature != null || s.humidity != null
          );

        if (!cancelled) setStations(results);
      } catch (err: any) {
        logger.warn('Error loading weather stations:', err);
        if (!cancelled) setError(err?.message || 'Error loading stations');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="bg-white rounded-2xl shadow-lg p-6 text-center">
        <Loader2 className="w-6 h-6 animate-spin text-sky-600 mx-auto" />
        <p className="text-sm text-gray-500 mt-2">Cargando estaciones...</p>
      </div>
    );
  }

  if (error || stations.length === 0) {
    return null; // silent — the page still works without stations
  }

  return (
    <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
      <div className="bg-gradient-to-r from-sky-500 to-sky-600 px-6 py-4">
        <h2 className="text-lg font-bold text-white flex items-center gap-2">
          <Cloud className="w-5 h-5" />
          Estaciones Meteorológicas
        </h2>
        <p className="text-xs text-sky-100 mt-1">
          {stations.length} {stations.length === 1 ? 'estación registrada' : 'estaciones registradas'}
        </p>
      </div>
      <div className="p-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {stations.map((s) => (
            <div
              key={s.id}
              className="bg-gray-50 rounded-xl p-4 border border-gray-100 hover:border-sky-200 transition"
            >
              <p className="text-sm font-semibold text-gray-800 truncate mb-3">
                {s.name}
              </p>
              <div className="space-y-1.5">
                {s.temperature != null && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500 flex items-center gap-1">
                      <Thermometer className="w-3.5 h-3.5 text-orange-500" />
                      Temp
                    </span>
                    <span className="font-medium">{s.temperature.toFixed(1)}°C</span>
                  </div>
                )}
                {s.humidity != null && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500 flex items-center gap-1">
                      <Droplets className="w-3.5 h-3.5 text-blue-500" />
                      Hum
                    </span>
                    <span className="font-medium">{s.humidity.toFixed(0)}%</span>
                  </div>
                )}
                {s.windSpeed != null && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500 flex items-center gap-1">
                      <Wind className="w-3.5 h-3.5 text-gray-500" />
                      Viento
                    </span>
                    <span className="font-medium">{(s.windSpeed * 3.6).toFixed(1)} km/h</span>
                  </div>
                )}
              </div>
              {s.lastObserved && (
                <p className="text-xs text-gray-400 mt-2">
                  {new Date(s.lastObserved).toLocaleString('es-ES', {
                    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                  })}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

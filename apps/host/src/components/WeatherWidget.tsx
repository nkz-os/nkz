// =============================================================================
// Weather Widget Component - AEMET Integration
// =============================================================================
// Widget para mostrar información meteorológica de AEMET en el dashboard
// =============================================================================

import React, { useState, useEffect } from 'react';
import { Cloud, Thermometer, Droplets, Wind, MapPin, Search, Loader2, AlertCircle, RefreshCw, Sprout } from 'lucide-react';
import api from '@/services/api';
import { useI18n } from '@/context/I18nContext';
import { useTenantMunicipality } from '@/hooks/useTenantMunicipality';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface WeatherData {
  observed_at: string;
  temp_avg?: number;
  temp_min?: number;
  temp_max?: number;
  humidity_avg?: number;
  precip_mm?: number;
  solar_rad_w_m2?: number;
  eto_mm?: number;
  wind_speed_ms?: number;
  wind_direction_deg?: number;
  pressure_hpa?: number;
  gdd_accumulated?: number;
  viento?: {
    direccion: string;
    velocidad: number;
  };
}

interface ForecastData {
  fecha: string;
  t_maxima: number;
  t_minima: number;
  estado_cielo?: string;
  precipitacion_proba?: number;
}

interface ParcelOption {
  id: string;
  name: string;
}

interface WeatherWidgetProps {
  municipalityCode?: string;
  municipalityName?: string;
  latitude?: number;
  longitude?: number;
  parcelId?: string;
  onMunicipalitySelect?: (code: string, name: string) => void;
  onParcelSelect?: (parcelId: string, parcelName: string) => void;
}

export const WeatherWidget: React.FC<WeatherWidgetProps> = ({
  municipalityCode,
  municipalityName,
  latitude: _latitude,
  longitude: _longitude,
  parcelId,
  onMunicipalitySelect,
  onParcelSelect,
}) => {
  const { t } = useI18n();
  
  // Auto-detect municipality from tenant if not provided
  const { municipality: tenantMunicipality } = useTenantMunicipality();
  
  const [weatherData, setWeatherData] = useState<WeatherData | null>(null);
  const [forecast, setForecast] = useState<ForecastData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downscaling, setDownscaling] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Array<{
    alert_type: string;
    alert_category: string;
    effective_from: string;
    effective_to: string;
    description: string;
  }>>([]);
  const [selectedMunicipalityName, setSelectedMunicipalityName] = useState<string | null>(
    municipalityName || tenantMunicipality?.name || null
  );

  // Parcel state
  const [parcels, setParcels] = useState<ParcelOption[]>([]);
  const [loadingParcels, setLoadingParcels] = useState(false);
  const [showParcelSearch, setShowParcelSearch] = useState(false);
  const [parcelSearchTerm, setParcelSearchTerm] = useState('');
  const [localParcelId, setLocalParcelId] = useState<string | undefined>(undefined);
  const [localParcelName, setLocalParcelName] = useState<string | null>(null);
  
  // Determine which municipality code to use (priority: prop > tenant)
  const effectiveMunicipalityCode = municipalityCode || tenantMunicipality?.code;
  const effectiveParcelId = parcelId || localParcelId;

  // Fetch parcels on mount
  useEffect(() => {
    let cancelled = false;
    const loadParcels = async () => {
      setLoadingParcels(true);
      try {
        const result = await api.getParcels();
        if (!cancelled) {
          const items: ParcelOption[] = (result || [])
            .filter((e: any) => e?.id)
            .map((e: any) => ({
              id: e.id,
              name: e.name?.value || e.name || e.id?.split(':')?.pop() || 'Parcela',
            }));
          setParcels(items);
        }
      } catch (err) {
        logger.warn('[WeatherWidget] Error loading parcels:', err);
      } finally {
        if (!cancelled) setLoadingParcels(false);
      }
    };
    loadParcels();
    return () => { cancelled = true; };
  }, []);

  // Load weather data (parcel priority > municipality)
  useEffect(() => {
    if (effectiveParcelId) {
      loadWeatherByParcel(effectiveParcelId);
    } else if (effectiveMunicipalityCode) {
      loadWeatherByMunicipality(
        effectiveMunicipalityCode,
        municipalityName || tenantMunicipality?.name,
        tenantMunicipality?.province
      );
    } else {
      loadWeatherFromPrimaryLocation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveParcelId, effectiveMunicipalityCode, municipalityCode, tenantMunicipality]);

  const loadWeatherFromPrimaryLocation = async () => {
    setLoading(true);
    setError(null);

    try {
      // Get tenant weather locations and use primary one
      const locations = await api.getWeatherLocations();
      const primaryLocation = locations.find((loc: any) => loc.is_primary) || locations[0];
      
      if (primaryLocation) {
        await loadWeatherByMunicipality(primaryLocation.municipality_code, primaryLocation.municipality_name);
      } else {
        // No error if no location - just show empty state
        setError(null);
        setWeatherData(null);
        setForecast([]);
      }
    } catch (err: any) {
      logger.error('Error loading weather from primary location:', err);
      // Don't show error if it's just that there's no location configured
      if (err.response?.status !== 404) {
      setError(err.message || t('weather.error_loading'));
      } else {
        setError(null);
        setWeatherData(null);
        setForecast([]);
      }
    } finally {
      setLoading(false);
    }
  };

  const loadWeatherByParcel = async (pid: string) => {
    setLoading(true);
    setError(null);
    try {
      const [historyRes, forecastRes] = await Promise.all([
        api.getParcelWeather(pid, { source: 'OPEN-METEO', data_type: 'HISTORY', limit: 1 }),
        api.getParcelWeather(pid, { source: 'OPEN-METEO', data_type: 'FORECAST', limit: 72 }),
      ]);

      const observations = historyRes?.observations || [];
      const forecastObs = forecastRes?.observations || [];
      setDownscaling(historyRes?.downscaling || null);

      if (observations.length > 0) {
        const latest = observations[0];
        setWeatherData({
          observed_at: latest.observed_at,
          temp_avg: latest.temp_avg,
          temp_min: latest.temp_min,
          temp_max: latest.temp_max,
          humidity_avg: latest.humidity_avg,
          precip_mm: latest.precip_mm,
          pressure_hpa: latest.pressure_hpa,
          viento: {
            direccion: latest.wind_direction_deg ? `${latest.wind_direction_deg}°` : 'N',
            velocidad: latest.wind_speed_ms ? Math.round(latest.wind_speed_ms * 3.6) : 0,
          },
        });
      } else {
        setWeatherData(null);
      }

      // Process forecast
      if (forecastObs.length > 0) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const maxDate = new Date(today);
        maxDate.setDate(maxDate.getDate() + 5);

        const dailyData = new Map<string, { temps: number[]; precip: number[] }>();
        forecastObs.forEach((obs: any) => {
          if (!obs.observed_at) return;
          const obsDate = new Date(obs.observed_at);
          if (isNaN(obsDate.getTime())) return;
          const obsDateOnly = new Date(obsDate.getFullYear(), obsDate.getMonth(), obsDate.getDate());
          const dateKey = obsDateOnly.toISOString().split('T')[0];
          const todayTime = today.getTime();
          const obsTime = obsDateOnly.getTime();
          if (obsTime >= todayTime && obsTime <= maxDate.getTime()) {
            if (!dailyData.has(dateKey)) dailyData.set(dateKey, { temps: [], precip: [] });
            const dayData = dailyData.get(dateKey)!;
            if (obs.temp_avg != null) dayData.temps.push(obs.temp_avg);
            if (obs.temp_min != null) dayData.temps.push(obs.temp_min);
            if (obs.temp_max != null) dayData.temps.push(obs.temp_max);
            if (obs.precip_mm != null && obs.precip_mm > 0) dayData.precip.push(obs.precip_mm);
          }
        });

        const forecastTransformed = Array.from(dailyData.entries())
          .map(([dateKey, dayData]) => {
            const dateOnly = new Date(dateKey + 'T00:00:00');
            const temps = dayData.temps.filter(t => t != null);
            const tMax = temps.length > 0 ? Math.max(...temps) : 0;
            const tMin = temps.length > 0 ? Math.min(...temps) : 0;
            const totalPrecip = dayData.precip.reduce((s, p) => s + p, 0);
            return {
              fecha: dateKey,
              fechaDate: new Date(dateOnly.getFullYear(), dateOnly.getMonth(), dateOnly.getDate()),
              t_maxima: tMax,
              t_minima: tMin,
              estado_cielo: totalPrecip > 0 ? 'Lluvia' : 'Despejado',
              precipitacion_proba: totalPrecip > 0 ? Math.min(100, totalPrecip * 10) : 0,
            };
          })
          .filter(item => item.fechaDate.getTime() >= today.getTime())
          .sort((a, b) => new Date(a.fecha).getTime() - new Date(b.fecha).getTime())
          .slice(0, 5);

        setForecast(forecastTransformed);
      } else {
        setForecast([]);
      }

      // Load alerts for parcel's municipality
      if (historyRes?.municipality_code) {
        try {
          const alertData = await api.getWeatherAlerts({ municipality_code: historyRes.municipality_code });
          setAlerts(alertData.alerts || []);
        } catch {
          setAlerts([]);
        }
      }
    } catch (err: any) {
      logger.warn('[WeatherWidget] Parcel weather failed, falling back to municipality:', err);
      if (effectiveMunicipalityCode) {
        await loadWeatherByMunicipality(
          effectiveMunicipalityCode,
          municipalityName || tenantMunicipality?.name,
          tenantMunicipality?.province
        );
      }
    } finally {
      setLoading(false);
    }
  };

  const handleParcelSelect = (id: string, name: string) => {
    setLocalParcelId(id);
    setLocalParcelName(name);
    setShowParcelSearch(false);
    setParcelSearchTerm('');
    if (onParcelSelect) {
      onParcelSelect(id, name);
    }
  };

  const loadWeatherByMunicipality = async (code?: string, name?: string, _province?: string) => {
    const targetCode = code || municipalityCode;
    if (!targetCode) return;

    setLoading(true);
    setError(null);

    try {
      // Municipality-based query
      setDownscaling(null);
      const observations = await api.getLatestWeatherObservations({
        municipality_code: targetCode,
        source: 'OPEN-METEO',
        data_type: 'HISTORY',
      });

      // Get forecast data
      let forecastData: { observations?: any[]; count?: number } | null = null;
      try {
        forecastData = await api.getWeatherObservations({
          municipality_code: targetCode,
          source: 'OPEN-METEO',
          data_type: 'FORECAST',
          limit: 200,
        });
      } catch (forecastErr) {
        logger.warn('Error fetching forecast data:', forecastErr);
      }

      // No data in DB for this municipality — show clean empty state
      if (observations.length === 0 && (!forecastData || forecastData.observations?.length === 0)) {
        logger.debug('[WeatherWidget] No weather data available for municipality', {
          observationsCount: observations.length,
          forecastDataCount: forecastData?.observations?.length || 0
        });
        setWeatherData(null);
        setForecast([]);
        setLoading(false);
        return;
      }

      // Process data from DB
      if (observations.length > 0) {
        const latest = observations[0];
        if (name) setSelectedMunicipalityName(name);
        // Transform to widget format
        setWeatherData({
          observed_at: latest.observed_at,
          temp_avg: latest.temp_avg,
          temp_min: latest.temp_min,
          temp_max: latest.temp_max,
          humidity_avg: latest.humidity_avg,
          precip_mm: latest.precip_mm,
          pressure_hpa: latest.pressure_hpa,
          viento: {
            direccion: latest.wind_direction_deg ? `${latest.wind_direction_deg}°` : 'N',
            velocidad: latest.wind_speed_ms ? Math.round(latest.wind_speed_ms * 3.6) : 0, // Convert m/s to km/h
          },
        });
      }

      // Process forecast from DB
      const forecastObservations = forecastData?.observations || [];

      let forecastProcessed = false;
      if (forecastObservations.length > 0) {
        // Transform forecast to widget format
        // Group hourly observations by date and calculate daily min/max
        const nowForDB = new Date();
        const todayForDB = new Date(nowForDB.getFullYear(), nowForDB.getMonth(), nowForDB.getDate());
        const maxDate = new Date(todayForDB);
        maxDate.setDate(maxDate.getDate() + 5);

        const dailyData = new Map<string, {
          temps: number[];
          precip: number[];
          weatherCodes: string[];
        }>();

        forecastObservations.forEach((obs: any) => {
          if (!obs.observed_at) return;
          
          const obsDate = new Date(obs.observed_at);
          if (isNaN(obsDate.getTime())) {
            logger.warn('[WeatherWidget] Invalid date in forecast observation:', obs.observed_at);
            return;
          }
          
          // Get date only (ignore time)
          const obsDateOnly = new Date(obsDate.getFullYear(), obsDate.getMonth(), obsDate.getDate());
          const dateKey = obsDateOnly.toISOString().split('T')[0];
          
          // Only include today and future dates (exclude yesterday)
          // Compare dates properly to avoid timezone issues
          const todayTime = todayForDB.getTime();
          const obsTime = obsDateOnly.getTime();
          if (obsTime >= todayTime && obsTime <= maxDate.getTime()) {
            if (!dailyData.has(dateKey)) {
              dailyData.set(dateKey, { temps: [], precip: [], weatherCodes: [] });
            }
            
            const dayData = dailyData.get(dateKey)!;
            
            // Collect temperatures (use temp_avg if temp_min/max not available)
            if (obs.temp_avg !== null && obs.temp_avg !== undefined) {
              dayData.temps.push(obs.temp_avg);
            }
            if (obs.temp_min !== null && obs.temp_min !== undefined) {
              dayData.temps.push(obs.temp_min);
            }
            if (obs.temp_max !== null && obs.temp_max !== undefined) {
              dayData.temps.push(obs.temp_max);
            }
            
            // Collect precipitation
            if (obs.precip_mm !== null && obs.precip_mm !== undefined && obs.precip_mm > 0) {
              dayData.precip.push(obs.precip_mm);
            }
            
            // Collect weather codes
            if (obs.metadata?.weather_code) {
              dayData.weatherCodes.push(obs.metadata.weather_code);
            }
          }
        });
        
        // Transform to forecast format (calculate min/max from collected temps)
        const nowForDBTransform = new Date();
        const todayForDBTransform = new Date(nowForDBTransform.getFullYear(), nowForDBTransform.getMonth(), nowForDBTransform.getDate());
        const todayTime = todayForDBTransform.getTime();
        
        const forecastTransformed = Array.from(dailyData.entries())
          .map(([dateKey, dayData]) => {
            const dateOnly = new Date(dateKey + 'T00:00:00');
            const temps = dayData.temps.filter(t => t !== null && t !== undefined);
            const t_maxima = temps.length > 0 ? Math.max(...temps) : null;
            const t_minima = temps.length > 0 ? Math.min(...temps) : null;
            const totalPrecip = dayData.precip.reduce((sum, p) => sum + p, 0);
            const hasPrecip = totalPrecip > 0;
            
            return {
              fecha: dateKey,
              fechaDate: new Date(dateOnly.getFullYear(), dateOnly.getMonth(), dateOnly.getDate()),
              t_maxima: t_maxima || 0,
              t_minima: t_minima || 0,
              estado_cielo: hasPrecip ? 'Lluvia' : 'Despejado',
              precipitacion_proba: hasPrecip ? Math.min(100, totalPrecip * 10) : 0,
            };
          })
          .filter(item => item.fechaDate.getTime() >= todayTime) // Exclude yesterday explicitly
          .sort((a, b) => new Date(a.fecha).getTime() - new Date(b.fecha).getTime())
          .slice(0, 5); // Get first 5 days
        
        if (forecastTransformed.length >= 5) {
          setForecast(forecastTransformed);
          forecastProcessed = true;
          logger.debug('[WeatherWidget] Forecast successfully processed from DB:', forecastTransformed.length, 'days');
        } else if (forecastTransformed.length > 0) {
          logger.debug('[WeatherWidget] Forecast from DB has fewer than 5 days (', forecastTransformed.length, ')');
          setForecast(forecastTransformed);
        } else {
          logger.warn(`[WeatherWidget] Forecast empty. Total observations: ${forecastObservations.length}`);
          logger.debug('[WeatherWidget] Daily data keys:', Array.from(dailyData.keys()));
        }
      }

      // Forecast not in DB — keep whatever partial forecast we got, if any
      if (!forecastProcessed && forecast.length === 0) {
        logger.debug('[WeatherWidget] No forecast data available for municipality');
      }

      // Update municipality name if provided
      if (name && onMunicipalitySelect) {
        onMunicipalitySelect(targetCode, name);
      }

      // Load active weather alerts for this municipality
      try {
        const alertData = await api.getWeatherAlerts({ municipality_code: targetCode });
        setAlerts(alertData.alerts || []);
      } catch (alertErr) {
        logger.warn('Error fetching weather alerts:', alertErr);
        setAlerts([]);
      }
    } catch (err: any) {
      logger.error('Error loading weather by municipality:', err);
      const errorMessage = err.response?.data?.detail || err.message || t('weather.error_loading');
      setError(errorMessage);
      // Clear data on error
      setWeatherData(null);
      setForecast([]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-500 to-blue-600 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Cloud className="w-6 h-6 text-white" />
            <div>
              <h2 className="text-xl font-bold text-white">{t('weather.widget_title')}</h2>
              <p className="text-sm text-blue-100">
                {localParcelName
                  ? `${localParcelName}`
                  : selectedMunicipalityName || municipalityName || tenantMunicipality?.name || t('weather.widget_subtitle_select')}
                {!localParcelName && tenantMunicipality?.province ? ` (${tenantMunicipality?.province})` : ''}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <Button
              onClick={() => setShowParcelSearch(!showParcelSearch)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white text-sm flex items-center gap-2"
            >
              <Sprout className="w-4 h-4" />
              {effectiveParcelId
                ? t('weather.change_parcel')
                : t('weather.select_parcel')}
            </Button>
            <Button
              onClick={() => {
                if (effectiveParcelId) {
                  loadWeatherByParcel(effectiveParcelId);
                } else if (effectiveMunicipalityCode) {
                  loadWeatherByMunicipality(
                    effectiveMunicipalityCode,
                    municipalityName || tenantMunicipality?.name,
                    tenantMunicipality?.province
                  );
                }
              }}
              disabled={loading || (!effectiveParcelId && !effectiveMunicipalityCode)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </div>

      {/* Parcel Search */}
      {showParcelSearch && (
        <div className="p-4 bg-nkz-bg-secondary border-b">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-nkz-muted" />
            <Input
              type="text"
              value={parcelSearchTerm}
              onChange={(e: any) => setParcelSearchTerm(e.target.value)}
              placeholder={t('weather.search_parcel_placeholder')}
              className="w-full pl-10 pr-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {loadingParcels && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-nkz-muted animate-spin" />
            )}
          </div>
          {loadingParcels ? (
            <div className="mt-2 p-4 text-center text-nkz-muted">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
              <p className="text-sm">{t('weather.loading_parcels')}</p>
            </div>
          ) : parcels.length > 0 ? (
            <div className="mt-2 max-h-64 overflow-y-auto border border-nkz-border rounded-lg bg-white shadow-lg">
              {parcels
                .filter(p => !parcelSearchTerm || p.name.toLowerCase().includes(parcelSearchTerm.toLowerCase()))
                .map((parcel) => (
                  <Button
                    key={parcel.id}
                    onClick={() => handleParcelSelect(parcel.id, parcel.name)}
                    className={`w-full px-4 py-2 text-left hover:bg-nkz-success-light transition flex items-center gap-2 border-b border-gray-100 last:border-b-0 ${
                      effectiveParcelId === parcel.id ? 'bg-nkz-success-light font-medium' : ''
                    }`}
                  >
                    <MapPin className="w-4 h-4 text-green-500 flex-shrink-0" />
                    <span className="text-sm text-gray-900 truncate">{parcel.name}</span>
                  </Button>
                ))}
              {parcels.filter(p => !parcelSearchTerm || p.name.toLowerCase().includes(parcelSearchTerm.toLowerCase())).length === 0 && (
                <div className="p-3 text-sm text-nkz-muted text-center">
                  {t('weather.no_parcels_found')}
                </div>
              )}
            </div>
          ) : (
            <div className="mt-2 p-3 text-sm text-nkz-muted text-center bg-nkz-bg-secondary rounded-lg">
              {t('weather.no_parcels')}
            </div>
          )}
        </div>
      )}

      {/* Content */}
      <div className="p-6">
        {error && (
          <div className="mb-4 p-4 bg-nkz-error-light border border-red-200 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-nkz-error flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-red-800 text-sm font-medium">Error</p>
              <p className="text-nkz-error text-sm">{error}</p>
            </div>
          </div>
        )}

        {/* AEMET Weather Alerts */}
        {alerts.length > 0 && (
          <div className="mb-4 space-y-2">
            {alerts.map((alert, idx) => {
              const alertColors: Record<string, string> = {
                RED: 'bg-red-600 border-red-700',
                ORANGE: 'bg-orange-500 border-orange-600',
                YELLOW: 'bg-nkz-warning-light0 border-yellow-600',
              };
              const alertLabels: Record<string, string> = {
                RED: 'Alerta Roja',
                ORANGE: 'Alerta Naranja',
                YELLOW: 'Alerta Amarilla',
              };
              const bg = alertColors[alert.alert_type] || alertColors.YELLOW;
              const label = alertLabels[alert.alert_type] || alert.alert_type;
              return (
                <div
                  key={idx}
                  className={`${bg} text-white px-4 py-3 rounded-lg border flex items-start gap-3`}
                >
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="font-semibold text-sm">
                      {label} — {alert.alert_category || 'Meteorológica'}
                    </p>
                    {alert.description && (
                      <p className="text-xs mt-1 opacity-90 line-clamp-2">
                        {alert.description}
                      </p>
                    )}
                    <p className="text-xs mt-1 opacity-75">
                      {new Date(alert.effective_from).toLocaleString('es-ES', {
                        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                      })}
                      {alert.effective_to && ` — ${new Date(alert.effective_to).toLocaleString('es-ES', {
                        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                      })}`}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {loading && !weatherData ? (
          <div className="text-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-nkz-info mx-auto mb-4" />
              <p className="text-gray-600">{t('weather.loading')}</p>
          </div>
        ) : weatherData ? (
          <>
            {/* Current Weather */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="bg-gradient-to-br from-orange-50 to-orange-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Thermometer className="w-5 h-5 text-orange-600" />
                  <span className="text-xs font-medium text-orange-700">{t('weather.temperature')}</span>
                </div>
                <p className="text-2xl font-bold text-orange-900">{weatherData.temp_avg?.toFixed(1) || 'N/A'}°C</p>
              </div>

              <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Droplets className="w-5 h-5 text-nkz-info" />
                  <span className="text-xs font-medium text-nkz-info">{t('weather.humidity')}</span>
                </div>
                <p className="text-2xl font-bold text-blue-900">{weatherData.humidity_avg?.toFixed(0) || 'N/A'}%</p>
              </div>

              <div className="bg-gradient-to-br from-gray-50 to-gray-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Wind className="w-5 h-5 text-gray-600" />
                  <span className="text-xs font-medium text-gray-700">{t('weather.wind')}</span>
                </div>
                <p className="text-2xl font-bold text-gray-900">{weatherData.viento?.velocidad || weatherData.wind_speed_ms ? Math.round((weatherData.wind_speed_ms || 0) * 3.6) : 'N/A'} km/h</p>
                <p className="text-xs text-gray-600 mt-1">{weatherData.viento?.direccion || (weatherData.wind_direction_deg ? `${weatherData.wind_direction_deg}°` : 'N/A')}</p>
              </div>

              <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Cloud className="w-5 h-5 text-purple-600" />
                  <span className="text-xs font-medium text-purple-700">{t('weather.pressure')}</span>
                </div>
                <p className="text-2xl font-bold text-purple-900">{weatherData.pressure_hpa?.toFixed(0) || 'N/A'} hPa</p>
              </div>

              {weatherData.gdd_accumulated != null && (
                <div className="bg-gradient-to-br from-emerald-50 to-emerald-100 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Thermometer className="w-5 h-5 text-emerald-600" />
                    <span className="text-xs font-medium text-emerald-700">{t('weather.gdd')}</span>
                  </div>
                  <p className="text-2xl font-bold text-emerald-900">{weatherData.gdd_accumulated.toFixed(0)}°D</p>
                  <p className="text-xs text-emerald-600 mt-1">{t('weather.gdd_base_10')}</p>
                </div>
              )}

              {weatherData.eto_mm != null && (
                <div className="bg-gradient-to-br from-teal-50 to-teal-100 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Droplets className="w-5 h-5 text-teal-600" />
                    <span className="text-xs font-medium text-teal-700">{t('weather.et0')}</span>
                  </div>
                  <p className="text-2xl font-bold text-teal-900">{weatherData.eto_mm.toFixed(1)} mm</p>
                  <p className="text-xs text-teal-600 mt-1">{t('weather.et0_desc')}</p>
                </div>
              )}
            </div>

            {/* Downscaling indicator */}
            {downscaling === 'applied' && (
              <div className="mb-4 p-3 bg-nkz-success-light border border-green-200 rounded-lg flex items-center gap-2">
                <MapPin className="w-4 h-4 text-nkz-success" />
                <p className="text-xs text-green-800">
                  {t('weather.downscaling_active')}
                </p>
              </div>
            )}

            {/* Forecast */}
            {forecast.length > 0 ? (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3">{t('weather.forecast_5_days')}</h3>
                <div className="grid grid-cols-5 gap-2">
                  {forecast.slice(0, 5).map((day, idx) => (
                    <div key={idx} className="bg-nkz-bg-secondary rounded-lg p-3 text-center">
                      <p className="text-xs text-gray-600 mb-2">
                        {new Date(day.fecha).toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric' })}
                      </p>
                      <div className="flex items-center justify-center gap-2 mb-2">
                        <span className="text-lg font-bold text-gray-900">{day.t_maxima?.toFixed(0) || 'N/A'}°</span>
                        <span className="text-sm text-nkz-muted">{day.t_minima?.toFixed(0) || 'N/A'}°</span>
                      </div>
                      <p className="text-xs text-gray-600">{day.estado_cielo || 'Despejado'}</p>
                      {day.precipitacion_proba && day.precipitacion_proba > 0 && (
                        <p className="text-xs text-nkz-info mt-1">💧 {day.precipitacion_proba.toFixed(0)}%</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mt-4 p-3 bg-nkz-warning-light border border-yellow-200 rounded-lg">
                <p className="text-xs text-yellow-800">
                  ⚠️ Previsión no disponible. Los datos se están cargando en segundo plano.
                </p>
              </div>
            )}
          </>
        ) : (
          <div className="text-center py-12">
            <Cloud className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-gray-600 mb-4">{t('weather.no_data')}</p>
            <Button
              onClick={() => setShowParcelSearch(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
            >
              {t('weather.select_parcel')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};


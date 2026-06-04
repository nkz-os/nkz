// =============================================================================
// Telemetry Chart Component - Gráficos históricos con Chart.js
// =============================================================================

import React, { useState, useEffect } from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  TimeScale
} from 'chart.js';
import { Line, Bar } from 'react-chartjs-2';
import 'chartjs-adapter-date-fns';
import { es } from 'date-fns/locale';
import { TrendingUp, RefreshCw } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';

// Registrar componentes de Chart.js
ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
  TimeScale
);

interface TelemetryValue {
  observed_at: string;
  payload: Record<string, any>;
  metadata?: Record<string, any>;
}

interface TelemetryChartProps {
  deviceId: string;
  deviceName: string;
  measurementKey: string; // 'temperature', 'humidity', etc.
  measurementLabel?: string;
  unit?: string;
  chartType?: 'line' | 'bar';
  timeRange?: '1h' | '6h' | '24h' | '7d' | '30d';
  height?: number;
}

export const TelemetryChart: React.FC<TelemetryChartProps> = ({
  deviceId,
  deviceName,
  measurementKey,
  measurementLabel,
  unit,
  chartType = 'line',
  timeRange = '24h',
  height = 300
}) => {
  const { t } = useI18n();
  const [telemetryData, setTelemetryData] = useState<TelemetryValue[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTimeRange, setSelectedTimeRange] = useState<'1h' | '6h' | '24h' | '7d' | '30d'>(timeRange);

  useEffect(() => {
    loadTelemetryData();
  }, [deviceId, selectedTimeRange]);

  const loadTelemetryData = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const endTime = new Date();
      const startTime = new Date();

      // Calcular tiempo de inicio según rango seleccionado
      switch (selectedTimeRange) {
        case '1h':
          startTime.setHours(startTime.getHours() - 1);
          break;
        case '6h':
          startTime.setHours(startTime.getHours() - 6);
          break;
        case '24h':
          startTime.setHours(startTime.getHours() - 24);
          break;
        case '7d':
          startTime.setDate(startTime.getDate() - 7);
          break;
        case '30d':
          startTime.setDate(startTime.getDate() - 30);
          break;
      }

      const data = await api.getDeviceTelemetry(deviceId, {
        start_time: startTime.toISOString(),
        end_time: endTime.toISOString(),
        limit: selectedTimeRange === '30d' ? 1000 : 500
      });

      if (data && data.telemetry) {
        setTelemetryData(data.telemetry);
      } else {
        setTelemetryData([]);
      }
    } catch (err: any) {
      console.error('Error loading telemetry:', err);
      setError(err?.response?.data?.error || t('sensors.telemetry_error'));
      setTelemetryData([]);
    } finally {
      setIsLoading(false);
    }
  };

  const extractValue = (payload: Record<string, any>, key: string): number | null => {
    const value = payload[key] || 
                  payload[key.toLowerCase()] ||
                  payload[key.toUpperCase()];
    
    if (typeof value === 'number') return value;
    if (typeof value === 'object' && value?.value !== undefined) return value.value;
    return null;
  };

  const chartData = {
    labels: telemetryData.map(t => t.observed_at),
    datasets: [
      {
        label: measurementLabel || measurementKey,
        data: telemetryData.map(t => extractValue(t.payload, measurementKey)).filter(v => v !== null) as number[],
        borderColor: 'rgb(59, 130, 246)',
        backgroundColor: chartType === 'line' 
          ? 'rgba(59, 130, 246, 0.1)' 
          : 'rgba(59, 130, 246, 0.8)',
        fill: chartType === 'line',
        tension: 0.4,
        pointRadius: chartType === 'line' ? 2 : 0,
        pointHoverRadius: 4
      }
    ]
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: true,
        position: 'top' as const
      },
      tooltip: {
        mode: 'index' as const,
        intersect: false,
        callbacks: {
          label: (context: any) => {
            const value = context.parsed.y;
            return `${context.dataset.label}: ${value.toFixed(2)} ${unit || ''}`;
          }
        }
      }
    },
    scales: {
      x: {
        type: 'time' as const,
        time: {
          unit: (selectedTimeRange === '1h' || selectedTimeRange === '6h' ? 'minute' :
                selectedTimeRange === '24h' ? 'hour' : 'day') as 'minute' | 'hour' | 'day',
          displayFormats: {
            minute: 'HH:mm',
            hour: 'HH:mm',
            day: 'dd/MM'
          }
        },
        adapters: {
          date: {
            locale: es
          }
        },
        title: {
          display: true,
          text: t('sensors.time')
        }
      },
      y: {
        title: {
          display: true,
          text: `${measurementLabel || measurementKey} ${unit ? `(${unit})` : ''}`
        }
      }
    } as any
  };

  const timeRangeOptions: Array<{ value: '1h' | '6h' | '24h' | '7d' | '30d'; label: string }> = [
    { value: '1h', label: t('sensors.last_hour') },
    { value: '6h', label: t('sensors.last_6_hours') },
    { value: '24h', label: t('sensors.last_24_hours') },
    { value: '7d', label: t('sensors.last_7_days') },
    { value: '30d', label: t('sensors.last_30_days') }
  ];

  if (isLoading) {
    return (
      <div className="p-4 bg-white rounded-lg border border-nkz-border" style={{ height: `${height}px` }}>
        <div className="flex items-center justify-center h-full">
          <RefreshCw className="w-6 h-6 text-nkz-muted animate-spin" />
          <span className="ml-2 text-gray-600">{t('sensors.loading_chart')}</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-nkz-error-light rounded-lg border border-red-200" style={{ height: `${height}px` }}>
        <div className="flex items-center justify-center h-full">
          <span className="text-nkz-error">{error}</span>
        </div>
      </div>
    );
  }

  if (telemetryData.length === 0) {
    return (
      <div className="p-4 bg-white rounded-lg border border-nkz-border" style={{ height: `${height}px` }}>
        <div className="flex items-center justify-center h-full">
          <span className="text-nkz-muted">{t('sensors.no_historical_data')}</span>
        </div>
      </div>
    );
  }

  const ChartComponent = chartType === 'line' ? Line : Bar;

  return (
    <div className="p-4 bg-white rounded-lg border border-nkz-border">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-5 h-5 text-nkz-info" />
          <h3 className="text-lg font-semibold text-gray-900">
            {measurementLabel || measurementKey} - {deviceName}
          </h3>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedTimeRange}
            onChange={(e) => setSelectedTimeRange(e.target.value as any)}
            className="px-3 py-1 text-sm border border-nkz-border rounded-lg bg-white"
          >
            {timeRangeOptions.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <button
            onClick={loadTelemetryData}
            className="p-1 text-gray-600 hover:text-gray-900 transition"
            title={t('sensors.refresh')}
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div style={{ height: `${height}px` }}>
        <ChartComponent data={chartData} options={chartOptions} />
      </div>

      <div className="mt-2 text-xs text-nkz-muted text-center">
        {telemetryData.length} {t('sensors.data_points')}
      </div>
    </div>
  );
};


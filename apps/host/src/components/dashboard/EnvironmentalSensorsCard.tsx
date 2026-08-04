import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Thermometer, Gauge, Droplets, Plus } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import type { Sensor } from '@/types';
import { Button } from '@nekazari/ui-kit';

interface EnvironmentalSensorsCardProps {
  sensors: Sensor[];
  canManageDevices: boolean;
  onOpenWizard: (entityType: string) => void;
}

export const EnvironmentalSensorsCard: React.FC<EnvironmentalSensorsCardProps> = ({ sensors, canManageDevices, onOpenWizard }) => {
  const navigate = useNavigate();
  const { t } = useI18n();

  return (
    <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg overflow-hidden">
      <div className="bg-gradient-to-r from-green-500 to-green-600 px-6 py-4">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <Thermometer className="w-6 h-6" />
          {t('dashboard.environmental_sensors')}
        </h2>
      </div>

      <div className="p-6">
        {sensors.length === 0 ? (
          <div className="text-center py-12">
            <Gauge className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-nkz-muted mb-4">{t('sensors.no_sensors_registered')}</p>
            {canManageDevices && (
              <Button
                onClick={() => onOpenWizard('AgriSensor')}
                className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition flex items-center gap-2 mx-auto"
              >
                <Plus className="w-4 h-4" />
                {t('sensors.new_sensor')}
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 force-grid">
            {sensors.slice(0, 4).map((sensor) => (
              <div key={sensor.id} className="bg-gradient-to-br from-gray-50 to-gray-100 rounded-xl p-4 hover:shadow-md transition">
                <div className="flex items-center gap-2 mb-3">
                  <Thermometer className="w-5 h-5 text-nkz-success" />
                  <span className="text-xs font-medium text-gray-600 truncate">
                    {sensor.name?.value || sensor.id}
                  </span>
                </div>

                {sensor.temperature && (
                  <div className="mb-2">
                    <p className="text-2xl font-bold text-gray-900">
                      {sensor.temperature.value}°C
                    </p>
                    <p className="text-xs text-nkz-muted">{t('weather.temperature')}</p>
                  </div>
                )}

                {sensor.moisture && (
                  <div className="flex items-center gap-2 text-sm">
                    <Droplets className="w-4 h-4 text-nkz-info" />
                    <span className="text-gray-700">{sensor.moisture.value}{t('dashboard.sensors.humidity_unit_label')}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {sensors.length > 4 && (
          <Button
            onClick={() => navigate('/sensors')}
            className="w-full mt-4 py-3 text-nkz-success-strong hover:bg-nkz-success-soft rounded-xl transition font-medium"
          >
            {t('dashboard.sensors.view_all_count', { count: sensors.length })}
          </Button>
        )}
      </div>
    </div>
  );
};

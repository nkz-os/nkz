import React from 'react';
import { AlertCircle, Activity, Battery, Zap } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';

interface AlertsCardProps {
  chargingRobots: number;
}

export const AlertsCard: React.FC<AlertsCardProps> = ({ chargingRobots }) => {
  const { t } = useI18n();
  return (
    <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
      <div className="px-6 py-4 border-b border-nkz-border">
        <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
          <AlertCircle className="w-6 h-6 text-orange-500" />
          {t('dashboard.alerts.title')}
        </h2>
      </div>

      <div className="p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-nkz-success-light border border-green-200 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-8 h-8 bg-nkz-success-light0 rounded-lg flex items-center justify-center">
                <Activity className="w-4 h-4 text-white" />
              </div>
              <span className="font-semibold text-green-900">{t('dashboard.alerts.all_ok')}</span>
            </div>
            <p className="text-sm text-nkz-success">
              {t('dashboard.alerts.all_systems_ok')}
            </p>
          </div>

          {chargingRobots > 0 && (
            <div className="bg-nkz-warning-light border border-yellow-200 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <div className="w-8 h-8 bg-nkz-warning-light0 rounded-lg flex items-center justify-center">
                  <Battery className="w-4 h-4 text-white" />
                </div>
                <span className="font-semibold text-yellow-900">{t('dashboard.alerts.charging')}</span>
              </div>
              <p className="text-sm text-nkz-warning">
                {t('dashboard.alerts.robots_charging', { count: chargingRobots })}
              </p>
            </div>
          )}

          <div className="bg-nkz-info-light border border-blue-200 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-8 h-8 bg-nkz-info-light0 rounded-lg flex items-center justify-center">
                <Zap className="w-4 h-4 text-white" />
              </div>
              <span className="font-semibold text-blue-900">{t('dashboard.alerts.online')}</span>
            </div>
            <p className="text-sm text-nkz-info">
              {t('dashboard.alerts.mqtt_stable')}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

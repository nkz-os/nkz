// =============================================================================
// Device Commands Component - Envío de comandos bidireccionales
// =============================================================================

import React, { useState, useEffect } from 'react';
import { Send, History, CheckCircle, XCircle, Clock, AlertCircle } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import { logger } from '@/utils/logger';
import { Button } from '@nekazari/ui-kit';


/* eslint-disable @typescript-eslint/no-explicit-any */
interface Command {
  id: string;
  command_type: string;
  payload: Record<string, any>;
  status: 'pending' | 'sent' | 'executed' | 'failed';
  sent_at: string;
  executed_at?: string;
  response?: Record<string, any>;
}

interface DeviceCommandsProps {
  deviceId: string;
  deviceName: string;
  mqttTopics?: {
    commands: string;
  };
}

export const DeviceCommands: React.FC<DeviceCommandsProps> = ({
  deviceId,
  deviceName: _deviceName,
  mqttTopics
}) => {
  const { t } = useI18n();
  const [commandHistory, setCommandHistory] = useState<Command[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [commandType, setCommandType] = useState('custom');
  const [commandPayload, setCommandPayload] = useState('{}');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Comandos predefinidos comunes
  const predefinedCommands = [
    { type: 'reboot', label: t('sensors.command_reboot'), payload: { action: 'reboot' } },
    { type: 'reset', label: t('sensors.command_reset'), payload: { action: 'factory_reset' } },
    { type: 'calibrate', label: t('sensors.command_calibrate'), payload: { action: 'calibrate' } },
    { type: 'update_firmware', label: t('sensors.command_update_firmware'), payload: { action: 'update_firmware' } },
    { type: 'get_status', label: t('sensors.command_get_status'), payload: { action: 'get_status' } },
    { type: 'custom', label: t('sensors.command_custom'), payload: {} }
  ];

  useEffect(() => {
    loadCommandHistory();
  }, [deviceId]);

  const loadCommandHistory = async () => {
    setIsLoadingHistory(true);
    try {
      const data = await api.getDeviceCommands(deviceId);
      if (data && data.commands) {
        setCommandHistory(data.commands);
      }
    } catch (err: any) {
      logger.error('Error loading command history:', err);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const handleSendCommand = async () => {
    setIsLoading(true);
    setError(null);
    setSuccess(null);

    try {
      let payload: Record<string, any>;
      
      if (commandType === 'custom') {
        try {
          payload = JSON.parse(commandPayload);
        } catch (e) {
          setError(t('sensors.invalid_json'));
          setIsLoading(false);
          return;
        }
      } else {
        const cmd = predefinedCommands.find(c => c.type === commandType);
        payload = cmd?.payload || {};
      }

      await api.sendDeviceCommand(deviceId, {
        command_type: commandType,
        payload: payload
      });

      setSuccess(t('sensors.command_sent'));
      setCommandPayload('{}');
      
      // Recargar historial después de un breve delay
      setTimeout(() => {
        loadCommandHistory();
      }, 1000);

    } catch (err: any) {
      logger.error('Error sending command:', err);
      setError(err?.response?.data?.error || t('sensors.command_error'));
    } finally {
      setIsLoading(false);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'executed':
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case 'sent':
        return <Clock className="w-4 h-4 text-yellow-500" />;
      case 'failed':
        return <XCircle className="w-4 h-4 text-nkz-error" />;
      default:
        return <Clock className="w-4 h-4 text-nkz-muted" />;
    }
  };

  const formatTimestamp = (timestamp: string): string => {
    try {
      const date = new Date(timestamp);
      return date.toLocaleString('es-ES', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
      });
    } catch {
      return timestamp;
    }
  };

  return (
    <div className="space-y-4">
      {/* Panel de envío de comandos */}
      <div className="p-4 bg-white rounded-lg border border-nkz-border">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">
          {t('sensors.send_command')}
        </h3>

        {error && (
          <div className="mb-4 p-3 bg-nkz-error-light border border-red-200 rounded-lg flex items-center">
            <AlertCircle className="w-5 h-5 text-nkz-error mr-2" />
            <span className="text-nkz-error text-sm">{error}</span>
          </div>
        )}

        {success && (
          <div className="mb-4 p-3 bg-nkz-success-light border border-green-200 rounded-lg flex items-center">
            <CheckCircle className="w-5 h-5 text-green-500 mr-2" />
            <span className="text-nkz-success text-sm">{success}</span>
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {t('sensors.command_type')}
            </label>
            <select
              value={commandType}
              onChange={(e: any) => {
                setCommandType(e.target.value);
                const cmd = predefinedCommands.find(c => c.type === e.target.value);
                if (cmd && cmd.type !== 'custom') {
                  setCommandPayload(JSON.stringify(cmd.payload, null, 2));
                } else {
                  setCommandPayload('{}');
                }
              }}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              {predefinedCommands.map(cmd => (
                <option key={cmd.type} value={cmd.type}>{cmd.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {t('sensors.command_payload')}
            </label>
            <textarea
              value={commandPayload}
              onChange={(e: any) => setCommandPayload(e.target.value)}
              rows={6}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg font-mono text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              placeholder='{"action": "reboot", "delay": 5}'
            />
            <p className="mt-1 text-xs text-nkz-muted">
              {t('sensors.command_payload_hint')}
            </p>
          </div>

          {mqttTopics && (
            <div className="p-3 bg-nkz-info-light border border-blue-200 rounded-lg">
              <p className="text-xs text-blue-800">
                <strong>{t('sensors.mqtt_topic_commands')}</strong> {mqttTopics.commands}
              </p>
            </div>
          )}

          <Button
            onClick={handleSendCommand}
            disabled={isLoading}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {isLoading ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>{t('sensors.sending')}</span>
              </>
            ) : (
              <>
                <Send className="w-4 h-4" />
                <span>{t('sensors.send_command')}</span>
              </>
            )}
          </Button>
        </div>
      </div>

      {/* Historial de comandos */}
      <div className="p-4 bg-white rounded-lg border border-nkz-border">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
            <History className="w-5 h-5" />
            {t('sensors.command_history')}
          </h3>
          <Button
            onClick={loadCommandHistory}
            disabled={isLoadingHistory}
            className="p-2 text-gray-600 hover:text-gray-900 transition disabled:opacity-50"
            title={t('sensors.refresh')}
          >
            <History className="w-4 h-4" />
          </Button>
        </div>

        {isLoadingHistory ? (
          <div className="text-center py-8 text-nkz-muted">
            {t('sensors.loading')}
          </div>
        ) : commandHistory.length === 0 ? (
          <div className="text-center py-8 text-nkz-muted">
            {t('sensors.no_commands')}
          </div>
        ) : (
          <div className="space-y-2">
            {commandHistory.map((cmd) => (
              <div
                key={cmd.id}
                className="p-3 bg-nkz-bg-secondary rounded-lg border border-nkz-border hover:bg-nkz-bg-secondary transition"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      {getStatusIcon(cmd.status)}
                      <span className="font-medium text-gray-900">{cmd.command_type}</span>
                      <span className={`text-xs px-2 py-1 rounded ${
                        cmd.status === 'executed' ? 'bg-nkz-success-light text-nkz-success' :
                        cmd.status === 'sent' ? 'bg-nkz-warning-light text-nkz-warning' :
                        cmd.status === 'failed' ? 'bg-nkz-error-light text-nkz-error' :
                        'bg-nkz-bg-secondary text-gray-700'
                      }`}>
                        {cmd.status}
                      </span>
                    </div>
                    <div className="text-xs text-gray-600 mb-2">
                      {formatTimestamp(cmd.sent_at)}
                      {cmd.executed_at && ` • Ejecutado: ${formatTimestamp(cmd.executed_at)}`}
                    </div>
                    <details className="text-xs">
                      <summary className="cursor-pointer text-gray-600 hover:text-gray-900">
                        {t('sensors.view_payload')}
                      </summary>
                      <pre className="mt-2 p-2 bg-white rounded text-xs overflow-auto">
                        {JSON.stringify(cmd.payload, null, 2)}
                      </pre>
                      {cmd.response && (
                        <>
                          <p className="mt-2 font-medium">{t('sensors.response')}</p>
                          <pre className="mt-1 p-2 bg-white rounded text-xs overflow-auto">
                            {JSON.stringify(cmd.response, null, 2)}
                          </pre>
                        </>
                      )}
                    </details>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};


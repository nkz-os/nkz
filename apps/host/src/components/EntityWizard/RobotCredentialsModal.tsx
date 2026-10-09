import React, { useState } from 'react';
import { X, Copy, Check, Shield, Server, Wifi } from 'lucide-react';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

export interface RobotCredentials {
  robot_uuid: string;
  ros_namespace: string;
}

interface RobotCredentialsModalProps {
  isOpen: boolean;
  onClose: () => void;
  robotName: string;
  credentials: RobotCredentials;
}

export const RobotCredentialsModal: React.FC<RobotCredentialsModalProps> = ({
  isOpen,
  onClose,
  robotName,
  credentials
}) => {
  const { t } = useI18n();
  const [copiedField, setCopiedField] = useState<string | null>(null);

  if (!isOpen) return null;

  const copyToClipboard = (text: string, field: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const CredentialField: React.FC<{ label: string; value: string; fieldName: string }> = ({ label, value, fieldName }) => (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-gray-700">{label}</label>
      <div className="flex items-center gap-2">
        <Input
          type="text"
          value={value}
          readOnly
          className="flex-1 px-3 py-2 text-sm border border-nkz-border rounded bg-nkz-bg-secondary font-mono"
        />
        <Button
          type="button"
          onClick={() => copyToClipboard(value, fieldName)}
          className="px-3 py-2 border border-nkz-border rounded hover:bg-nkz-bg-secondary transition"
          title={t('wizard.robot_credentials.copy')}
        >
          {copiedField === fieldName
            ? <Check className="w-4 h-4 text-nkz-success-strong" />
            : <Copy className="w-4 h-4" />
          }
        </Button>
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full">
        {/* Header */}
        <div className="bg-gradient-to-r from-green-500 to-emerald-600 px-6 py-5 flex justify-between items-start rounded-t-2xl">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-white/20 rounded-lg">
              <Shield className="w-6 h-6 text-white" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-white">{t('wizard.robot_credentials.title')}</h2>
              <p className="text-sm text-green-100">
                <span className="font-semibold">{robotName}</span> {t('wizard.robot_credentials.ready_suffix')}
              </p>
            </div>
          </div>
          <Button onClick={onClose} className="text-white/70 hover:text-white p-1">
            <X className="w-6 h-6" />
          </Button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-5">
          {/* Robot info */}
          <div className="bg-nkz-bg-secondary rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <Server className="w-4 h-4 text-nkz-muted" />
              <h4 className="font-medium text-gray-700">{t('wizard.robot_credentials.identity')}</h4>
            </div>
            <div className="space-y-2">
              <CredentialField label="Robot UUID" value={credentials.robot_uuid} fieldName="uuid" />
              <CredentialField label="ROS Namespace" value={credentials.ros_namespace} fieldName="ros_namespace" />
            </div>
          </div>

          {/* Network provisioning notice */}
          <div className="bg-sky-50 border border-sky-200 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <Wifi className="w-4 h-4 text-sky-600" />
              <h4 className="font-medium text-sky-900">{t('wizard.robot_credentials.network_title')}</h4>
            </div>
            <p className="text-sm text-sky-800">
              {t('wizard.robot_credentials.network_before')}{' '}
              <a href="/connectivity" className="font-semibold underline hover:text-sky-600">
                {t('wizard.robot_credentials.device_management')}
              </a>{' '}
              {t('wizard.robot_credentials.network_after')}
            </p>
            <ol className="text-sm text-sky-800 space-y-1 list-decimal list-inside mt-2">
              <li>{t('wizard.robot_credentials.step1_before')} <strong>{t('wizard.robot_credentials.step1_strong')}</strong></li>
              <li>{t('wizard.robot_credentials.step2')}</li>
              <li>{t('wizard.robot_credentials.step3')}</li>
            </ol>
          </div>
        </div>

        {/* Footer */}
        <div className="bg-nkz-bg-secondary px-6 py-4 border-t rounded-b-2xl">
          <Button
            type="button"
            onClick={onClose}
            className="w-full px-4 py-3 bg-green-600 text-white rounded-xl hover:bg-green-700 transition font-medium"
          >
            {t('wizard.robot_credentials.done')}
          </Button>
        </div>
      </div>
    </div>
  );
};

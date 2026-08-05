// =============================================================================
// Copernicus BYOK Credentials Component
// =============================================================================
// Tenant self-service for Copernicus Data Space Ecosystem credentials.
// Calls the vegetation module backend: GET/PUT/DELETE /api/vegetation/config

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import { useConfirm } from '@/context/ConfirmContext';
import api from '@/services/api';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';
import {
  Cloud,
  Save,
  Trash2,
  AlertCircle,
  CheckCircle,
  RefreshCw,
  Eye,
  EyeOff,
  ExternalLink,
} from 'lucide-react';

interface CopernicusConfig {
  tenant_id: string;
  copernicus_client_id: string | null;
  copernicus_configured: boolean;
}

interface CredentialStatus {
  available: boolean;
  source: 'tenant' | 'platform' | null;
  message: string;
  client_id_preview: string | null;
}

interface UsageInfo {
  used: number;
  limit: number | null;
  remaining: number | null;
  period: string;
}

type EngineStatus = 'byok' | 'platform' | 'legacy';

const STATUS_LABELS: Record<EngineStatus, string> = {
  byok: 'settings.copernicus.status.byok',
  platform: 'settings.copernicus.status.platform',
  legacy: 'settings.copernicus.status.legacy',
};

const STATUS_HINTS: Record<EngineStatus, string> = {
  byok: 'settings.copernicus.status.byok_hint',
  platform: 'settings.copernicus.status.platform_hint',
  legacy: 'settings.copernicus.status.legacy_hint',
};

export const CopernicusCredentials: React.FC = () => {
  const { user, tenantId } = useAuth();
  const { t } = useI18n();
  const confirm = useConfirm();

  const [config, setConfig] = useState<CopernicusConfig | null>(null);
  const [credStatus, setCredStatus] = useState<CredentialStatus | null>(null);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [hasExistingSecret, setHasExistingSecret] = useState(false);

  const successTimerRef = useRef<ReturnType<typeof setTimeout>>();

  const isTenantAdmin = user?.roles?.includes('TenantAdmin');
  const isPlatformAdmin = user?.roles?.includes('PlatformAdmin');
  const canManageCredentials = isTenantAdmin || isPlatformAdmin;

  const engineStatus = getEngineStatus(config, credStatus);

  const clearSuccessTimer = useCallback(() => {
    if (successTimerRef.current) {
      clearTimeout(successTimerRef.current);
      successTimerRef.current = undefined;
    }
  }, []);

  const showTimedSuccess = useCallback((msg: string) => {
    clearSuccessTimer();
    setSuccess(msg);
    successTimerRef.current = setTimeout(() => setSuccess(null), 5000);
  }, [clearSuccessTimer]);

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/vegetation/config');
      const data = response.data;
      setConfig(data);
      if (data.copernicus_client_id) {
        setClientId(data.copernicus_client_id);
        setHasExistingSecret(!!data.copernicus_configured);
      }
    } catch (err: unknown) {
      if ((err as { response?: { status?: number } })?.response?.status !== 404) {
        setError(t('settings.copernicus.errors.load'));
        logger.error('Error loading Copernicus config:', err);
      }
    } finally {
      setLoading(false);
    }
  }, [t]);

  const loadCredentialStatus = useCallback(async () => {
    try {
      const response = await api.get('/api/vegetation/config/credentials-status');
      setCredStatus(response.data);
    } catch (err: unknown) {
      logger.debug('Credential status not available:', err);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      const response = await api.get('/api/vegetation/config/usage');
      setUsage(response.data);
    } catch (err: unknown) {
      logger.debug('Usage info not available:', err);
    }
  }, []);

  useEffect(() => {
    if (canManageCredentials && tenantId) {
      loadConfig();
      loadCredentialStatus();
      loadUsage();
    }
    return clearSuccessTimer;
  }, [canManageCredentials, tenantId, loadConfig, loadCredentialStatus, loadUsage, clearSuccessTimer]);

  const handleSave = async () => {
    if (!clientId.trim()) {
      setError(t('settings.copernicus.errors.client_id_required'));
      return;
    }
    if (!hasExistingSecret && !clientSecret.trim()) {
      setError(t('settings.copernicus.errors.secret_required'));
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      const payload: Record<string, string> = {
        copernicus_client_id: clientId.trim(),
      };
      if (clientSecret) {
        payload.copernicus_client_secret = clientSecret;
      }

      await api.put('/api/vegetation/config', payload);

      showTimedSuccess(t('settings.copernicus.saved'));
      setHasExistingSecret(true);
      setClientSecret('');
      await loadConfig();
      await loadCredentialStatus();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('settings.copernicus.errors.save');
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    const confirmed = await confirm({
      message: t('settings.copernicus.confirm_delete'),
      confirmLabel: t('settings.copernicus.delete'),
      cancelLabel: t('cancel'),
      tone: 'danger',
    });
    if (!confirmed) {
      return;
    }

    try {
      setDeleting(true);
      setError(null);
      setSuccess(null);
      await api.delete('/api/vegetation/config');
      showTimedSuccess(t('settings.copernicus.deleted'));
      setClientId('');
      setHasExistingSecret(false);
      await loadConfig();
      await loadCredentialStatus();
    } catch {
      setError(t('settings.copernicus.errors.delete'));
    } finally {
      setDeleting(false);
    }
  };

  // Render: read-only for non-admins
  if (!canManageCredentials) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
        <div className="flex items-center gap-3 mb-2">
          <Cloud className="w-5 h-5 text-nkz-info" />
          <span className="text-sm text-nkz-muted">
            {t('settings.copernicus.read_only')}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-nkz-info-soft rounded-lg flex items-center justify-center">
            <Cloud className="w-5 h-5 text-nkz-info" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              ☁️ {t('settings.copernicus.title')}
            </h2>
            <p className="text-sm text-gray-600">
              {t('settings.copernicus.description')}
            </p>
          </div>
        </div>
        <Button
          onClick={() => { loadConfig(); loadCredentialStatus(); }}
          disabled={loading}
          className="text-gray-500 hover:text-gray-700"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* Engine Status Badge */}
      <div className={`mb-4 p-3 rounded-lg border flex items-center gap-3 ${
        engineStatus === 'byok' ? 'bg-nkz-success-soft border-green-200' :
        engineStatus === 'platform' ? 'bg-nkz-warning-soft border-yellow-200' :
        'bg-nkz-bg-secondary border-nkz-border'
      }`}>
        <div className={`w-3 h-3 rounded-full ${
          engineStatus === 'byok' ? 'bg-green-500' :
          engineStatus === 'platform' ? 'bg-yellow-500' :
          'bg-gray-400'
        }`} />
        <div>
          <p className="text-sm font-medium">
            {t(STATUS_LABELS[engineStatus])}
          </p>
          <p className="text-xs text-gray-600 mt-0.5">
            {t(STATUS_HINTS[engineStatus])}
          </p>
        </div>
      </div>

      {/* Usage Meter */}
      {usage && (
        <div className={`mb-4 p-3 rounded-lg border ${
          usage.limit !== null && usage.remaining === 0
            ? 'bg-nkz-warning-soft border-yellow-200'
            : 'bg-nkz-bg-secondary border-nkz-border'
        }`}>
          <p className="text-sm font-medium text-gray-700">
            {usage.limit === null
              ? t('settings.copernicus.usageUnlimited', { used: usage.used })
              : t('settings.copernicus.usage', { used: usage.used, limit: usage.limit })}
          </p>
          {usage.limit !== null && usage.remaining === 0 && (
            <p className="text-xs text-nkz-warning-strong mt-0.5">
              {t('settings.copernicus.usageLimitReached')}
            </p>
          )}
        </div>
      )}

      {/* Error / Success */}
      {error && (
        <div className="mb-4 p-3 bg-nkz-danger-soft border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-nkz-danger-strong flex-shrink-0" />
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}
      {success && (
        <div className="mb-4 p-3 bg-nkz-success-soft border border-green-200 rounded-lg flex items-center gap-2">
          <CheckCircle className="w-4 h-4 text-nkz-success flex-shrink-0" />
          <p className="text-sm text-green-800">{success}</p>
        </div>
      )}

      {/* Form */}
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('settings.copernicus.client_id')}
          </label>
          <Input
            type="text"
            value={clientId}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setClientId(e.target.value)}
            placeholder="cdse-client-id-..."
            className="w-full font-mono text-sm"
            disabled={saving}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('settings.copernicus.client_secret')}
          </label>
          <div className="relative">
            <Input
              type={showSecret ? 'text' : 'password'}
              value={clientSecret}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => setClientSecret(e.target.value)}
              placeholder={hasExistingSecret
                ? t('settings.copernicus.secret_keep_hint')
                : t('settings.copernicus.secret_placeholder')}
              className="w-full font-mono text-sm pr-10"
              disabled={saving}
            />
            <button
              type="button"
              onClick={() => setShowSecret(!showSecret)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              tabIndex={-1}
            >
              {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          {hasExistingSecret && (
            <p className="text-xs text-nkz-muted mt-1">
              {t('settings.copernicus.secret_keep_hint')}
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2 pt-2">
          <Button
            onClick={handleSave}
            disabled={saving || !clientId.trim()}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm"
          >
            <Save className="w-4 h-4" />
            {saving ? t('settings.copernicus.saving') : t('settings.copernicus.save')}
          </Button>

          {(config?.copernicus_configured || hasExistingSecret) && (
            <Button
              onClick={handleDelete}
              disabled={deleting}
              className="flex items-center gap-2 px-4 py-2 text-red-600 bg-red-50 rounded-lg hover:bg-red-100 text-sm"
            >
              <Trash2 className="w-4 h-4" />
              {deleting ? t('settings.copernicus.deleting') : t('settings.copernicus.delete')}
            </Button>
          )}
        </div>

        {/* Info */}
        <div className="mt-4 p-3 bg-nkz-bg-secondary rounded-lg border border-nkz-border">
          <p className="text-xs text-gray-600">
            {t('settings.copernicus.register_hint')}{' '}
            <a
              href="https://dataspace.copernicus.eu/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-nkz-info hover:underline inline-flex items-center gap-1"
            >
              Copernicus Data Space Ecosystem <ExternalLink className="w-3 h-3" />
            </a>
            {t('settings.copernicus.register_hint_suffix')}
          </p>
          {tenantId && (
            <p className="text-xs text-gray-500 mt-1 font-mono">
              Tenant: {tenantId}
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

function getEngineStatus(
  config: CopernicusConfig | null,
  credStatus: CredentialStatus | null,
): EngineStatus {
  if (config?.copernicus_configured) return 'byok';
  if (credStatus?.available) return 'platform';
  return 'legacy';
}

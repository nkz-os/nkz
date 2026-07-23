// =============================================================================
// Copernicus BYOK Credentials Component
// =============================================================================
// Tenant self-service for Copernicus Data Space Ecosystem credentials.
// Calls vegetation-health backend: GET/PUT/DELETE /api/vegetation/config

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
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
  default_index_type: string;
  cloud_coverage_threshold: number;
  auto_process: boolean;
}

interface CredentialStatus {
  available: boolean;
  source: 'tenant' | 'platform' | null;
  message: string;
  client_id_preview: string | null;
}

type EngineStatus = 'byok' | 'platform' | 'legacy';

export const CopernicusCredentials: React.FC = () => {
  const { user, tenantId } = useAuth();
  const { t } = useI18n();

  const [config, setConfig] = useState<CopernicusConfig | null>(null);
  const [credStatus, setCredStatus] = useState<CredentialStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form state
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [showSecret, setShowSecret] = useState(false);
  const [hasExistingSecret, setHasExistingSecret] = useState(false);

  const isTenantAdmin = user?.roles?.includes('TenantAdmin');
  const isPlatformAdmin = user?.roles?.includes('PlatformAdmin');
  const canManageCredentials = isTenantAdmin || isPlatformAdmin;

  const engineStatus = getEngineStatus(config, credStatus);

  useEffect(() => {
    if (canManageCredentials && tenantId) {
      loadConfig();
      loadCredentialStatus();
    }
  }, [canManageCredentials, tenantId]);

  const loadConfig = async () => {
    try {
      setLoading(true);
      const response = await api.get('/api/vegetation/config', {
        headers: { 'X-Tenant-ID': tenantId },
      });
      const data = response.data;
      setConfig(data);
      if (data.copernicus_client_id) {
        setClientId(data.copernicus_client_id);
        setHasExistingSecret(!!data.copernicus_configured);
      }
    } catch (err: any) {
      // 404 = no config yet, that's OK
      if (err?.response?.status !== 404) {
        setError('Error al cargar configuración');
        logger.error('Error loading Copernicus config:', err);
      }
    } finally {
      setLoading(false);
    }
  };

  const loadCredentialStatus = async () => {
    try {
      const response = await api.get('/api/vegetation/config/credentials-status', {
        headers: { 'X-Tenant-ID': tenantId },
      });
      setCredStatus(response.data);
    } catch (err: any) {
      logger.debug('Credential status not available:', err);
    }
  };

  const handleSave = async () => {
    if (!clientId.trim() || !clientSecret.trim()) {
      setError('Client ID y Client Secret son obligatorios');
      return;
    }

    try {
      setSaving(true);
      setError(null);
      setSuccess(null);

      await api.put('/api/vegetation/config', {
        copernicus_client_id: clientId.trim(),
        copernicus_client_secret: clientSecret,
      }, {
        headers: { 'X-Tenant-ID': tenantId },
      });

      setSuccess('Credenciales guardadas. Sentinel Hub se activará en la próxima solicitud.');
      setHasExistingSecret(true);
      setClientSecret(''); // Clear secret from memory
      await loadConfig();
      await loadCredentialStatus();
      setTimeout(() => setSuccess(null), 5000);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Error al guardar credenciales';
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm('¿Eliminar tus credenciales de Copernicus? El sistema usará las credenciales de la plataforma si están disponibles.')) {
      return;
    }

    try {
      setError(null);
      setSuccess(null);
      await api.delete('/api/vegetation/config', {
        headers: { 'X-Tenant-ID': tenantId },
      });
      setSuccess('Credenciales eliminadas. Se usará el fallback de plataforma.');
      setClientId('');
      setHasExistingSecret(false);
      await loadConfig();
      await loadCredentialStatus();
      setTimeout(() => setSuccess(null), 5000);
    } catch (err: any) {
      setError('Error al eliminar credenciales');
    }
  };

  if (!canManageCredentials) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6 mb-6">
        <div className="flex items-center gap-3 mb-2">
          <Cloud className="w-5 h-5 text-nkz-info" />
          <span className="text-sm text-nkz-muted">
            Las credenciales de Copernicus son gestionadas por el administrador del tenant.
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
          <div className="w-10 h-10 bg-nkz-info-light rounded-lg flex items-center justify-center">
            <Cloud className="w-5 h-5 text-nkz-info" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              ☁️ Copernicus Data Space
            </h2>
            <p className="text-sm text-gray-600">
              {t('settings.copernicus.description', { defaultValue: 'Credenciales para Sentinel Hub (índices de vegetación)' })}
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
        engineStatus === 'byok' ? 'bg-nkz-success-light border-green-200' :
        engineStatus === 'platform' ? 'bg-nkz-warning-light border-yellow-200' :
        'bg-nkz-bg-secondary border-nkz-border'
      }`}>
        <div className={`w-3 h-3 rounded-full ${
          engineStatus === 'byok' ? 'bg-green-500' :
          engineStatus === 'platform' ? 'bg-yellow-500' :
          'bg-gray-400'
        }`} />
        <div>
          <p className="text-sm font-medium">
            {engineStatus === 'byok' && '🟢 Sentinel Hub activo — usando tus credenciales'}
            {engineStatus === 'platform' && '🟡 Sentinel Hub activo — usando credenciales de plataforma'}
            {engineStatus === 'legacy' && '⚪ Modo legacy — descarga y procesado local'}
          </p>
          <p className="text-xs text-gray-600 mt-0.5">
            {engineStatus === 'byok' && 'El consumo de Processing Units (PUs) va a tu cuenta de CDSE.'}
            {engineStatus === 'platform' && 'Consume PUs de la cuenta de plataforma. Configura tus propias credenciales para evitar límites compartidos.'}
            {engineStatus === 'legacy' && 'Sin credenciales configuradas. Contacta al administrador o añade tus claves CDSE.'}
          </p>
        </div>
      </div>

      {/* Error / Success */}
      {error && (
        <div className="mb-4 p-3 bg-nkz-error-light border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-nkz-error flex-shrink-0" />
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}
      {success && (
        <div className="mb-4 p-3 bg-nkz-success-light border border-green-200 rounded-lg flex items-center gap-2">
          <CheckCircle className="w-4 h-4 text-nkz-success flex-shrink-0" />
          <p className="text-sm text-green-800">{success}</p>
        </div>
      )}

      {/* Form */}
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Client ID
          </label>
          <Input
            type="text"
            value={clientId}
            onChange={(e: any) => setClientId(e.target.value)}
            placeholder="cdse-client-id-..."
            className="w-full font-mono text-sm"
            disabled={saving}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Client Secret
          </label>
          <div className="relative">
            <Input
              type={showSecret ? 'text' : 'password'}
              value={clientSecret}
              onChange={(e: any) => setClientSecret(e.target.value)}
              placeholder={hasExistingSecret ? '•••••••• (dejar vacío para no cambiar)' : 'cdse-secret-...'}
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
              Ya hay un secret guardado. Deja este campo vacío para conservarlo.
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
            {saving ? 'Guardando...' : 'Guardar credenciales'}
          </Button>

          {(config?.copernicus_configured || hasExistingSecret) && (
            <Button
              onClick={handleDelete}
              className="flex items-center gap-2 px-4 py-2 text-red-600 bg-red-50 rounded-lg hover:bg-red-100 text-sm"
            >
              <Trash2 className="w-4 h-4" />
              Eliminar
            </Button>
          )}
        </div>

        {/* Info */}
        <div className="mt-4 p-3 bg-nkz-bg-secondary rounded-lg border border-nkz-border">
          <p className="text-xs text-gray-600">
            <strong>¿No tienes credenciales?</strong> Regístrate en{' '}
            <a
              href="https://dataspace.copernicus.eu/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-nkz-info hover:underline inline-flex items-center gap-1"
            >
              Copernicus Data Space Ecosystem <ExternalLink className="w-3 h-3" />
            </a>
            {' '}y crea un OAuth Client. El consumo de Processing Units (PUs) irá a tu cuenta.
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
  // Tenant has its own BYOK credentials
  if (config?.copernicus_configured) return 'byok';
  // Platform fallback is available
  if (credStatus?.available) return 'platform';
  // Nothing configured → legacy
  return 'legacy';
}

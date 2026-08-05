// =============================================================================
// Risk Webhooks Panel
// =============================================================================
// Manage webhook registrations for risk push notifications

import React, { useState, useEffect } from 'react';
import { useI18n } from '@/context/I18nContext';
import { api } from '@/services/api';
import type { RiskWebhook } from '@/types';
import { Webhook, Plus, Trash2, X, ChevronUp } from 'lucide-react';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface RiskWebhooksPanelProps {
  readOnly?: boolean;
}

const SEVERITY_COLORS: Record<string, string> = {
  low: 'bg-nkz-bg-secondary text-gray-700',
  medium: 'bg-nkz-warning-soft text-yellow-800',
  high: 'bg-orange-100 text-orange-800',
  critical: 'bg-nkz-danger-soft text-red-800',
};

export const RiskWebhooksPanel: React.FC<RiskWebhooksPanelProps> = ({ readOnly = false }) => {
  const { t } = useI18n();
  const [webhooks, setWebhooks] = useState<RiskWebhook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [formName, setFormName] = useState('');
  const [formUrl, setFormUrl] = useState('');
  const [formSecret, setFormSecret] = useState('');
  const [formSeverity, setFormSeverity] = useState<string>('medium');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = async () => {
    try {
      setLoading(true);
      const data = await api.getRiskWebhooks();
      setWebhooks(data);
      setError(null);
    } catch {
      setError('Error al cargar los webhooks');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formName.trim() || !formUrl.trim()) {
      setFormError(t('settings.users.required_fields'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await api.createRiskWebhook({
        name: formName.trim(),
        url: formUrl.trim(),
        secret: formSecret.trim() || undefined,
        min_severity: formSeverity,
      });
      setFormName('');
      setFormUrl('');
      setFormSecret('');
      setFormSeverity('medium');
      setShowForm(false);
      await load();
    } catch {
      setFormError('Error al crear el webhook');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await api.deleteRiskWebhook(id);
      setWebhooks(prev => prev.filter(w => w.id !== id));
    } catch {
      setError('Error al eliminar el webhook');
    }
  };

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-nkz-border dark:border-gray-700 overflow-hidden">
      <div className="px-6 py-4 border-b border-nkz-border dark:border-gray-700 flex items-center justify-between">
        <h3 className="text-base font-semibold text-gray-900 dark:text-white flex items-center gap-2">
          <Webhook className="w-5 h-5 text-nkz-muted" />
          {t('settings.risk_webhooks.title')}
        </h3>
        {!readOnly && (
          <Button
            onClick={() => setShowForm(v => !v)}
            className="flex items-center gap-1 px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white text-sm rounded-lg transition"
          >
            {showForm ? <ChevronUp className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
            {showForm ? 'Cancelar' : '+ Nuevo'}
          </Button>
        )}
      </div>

      {/* Inline create form */}
      {showForm && !readOnly && (
        <form onSubmit={handleCreate} className="px-6 py-4 bg-nkz-bg-secondary dark:bg-gray-700/40 border-b border-nkz-border dark:border-gray-700 space-y-3">
          {formError && (
            <p className="text-sm text-nkz-danger-strong dark:text-red-400">{formError}</p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{t('settings.risk_webhooks.name')} *</label>
              <Input
                type="text"
                value={formName}
                onChange={(e: any) => setFormName(e.target.value)}
                placeholder="Mi webhook"
                required
                className="w-full px-3 py-2 text-sm border border-nkz-border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{t('settings.risk_webhooks.url')} *</label>
              <Input
                type="url"
                value={formUrl}
                onChange={(e: any) => setFormUrl(e.target.value)}
                placeholder="https://hooks.example.com/risk"
                required
                className="w-full px-3 py-2 text-sm border border-nkz-border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Secret (opcional)</label>
              <Input
                type="password"
                value={formSecret}
                onChange={(e: any) => setFormSecret(e.target.value)}
                placeholder="••••••••"
                className="w-full px-3 py-2 text-sm border border-nkz-border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">Severidad mínima</label>
              <select
                value={formSeverity}
                onChange={(e: any) => setFormSeverity(e.target.value)}
                className="w-full px-3 py-2 text-sm border border-nkz-border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white"
              >
                <option value="low">Baja</option>
                <option value="medium">Media</option>
                <option value="high">Alta</option>
                <option value="critical">Crítica</option>
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              onClick={() => setShowForm(false)}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 transition"
            >
              <X className="w-4 h-4 inline mr-1" />Cancelar
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg disabled:opacity-50 transition"
            >
              {submitting ? 'Guardando...' : 'Crear webhook'}
            </Button>
          </div>
        </form>
      )}

      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {loading ? (
          <div className="px-6 py-8 text-center">
            <div className="inline-block w-6 h-6 border-2 border-red-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div className="px-6 py-4 text-sm text-nkz-danger-strong dark:text-red-400">{error}</div>
        ) : webhooks.length === 0 ? (
          <div className="px-6 py-8 text-center text-sm text-nkz-muted dark:text-nkz-muted">
            <Webhook className="w-8 h-8 mx-auto mb-2 opacity-30" />
            <p>No hay webhooks registrados.</p>
            {!readOnly && (
              <p className="mt-1 text-xs">Haz clic en "+ Nuevo' para crear uno."</p>
            )}
          </div>
        ) : (
          webhooks.map(wh => (
            <div key={wh.id} className="px-6 py-3 flex items-center justify-between gap-4">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-gray-900 dark:text-white truncate">{wh.name}</p>
                <p className="text-xs text-nkz-muted dark:text-nkz-muted truncate">{wh.url}</p>
              </div>
              <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full ${SEVERITY_COLORS[wh.min_severity] || SEVERITY_COLORS.medium}`}>
                ≥ {wh.min_severity}
              </span>
              {!readOnly && (
                <Button
                  onClick={() => handleDelete(wh.id)}
                  className="shrink-0 p-1.5 text-nkz-muted hover:text-nkz-danger-strong transition rounded"
                  title={t('settings.users.delete')}
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, AlertTriangle } from 'lucide-react';
import client from '@/services/api';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface InventoryData {
  tenant_id: string;
  tenant_name: string;
  systems: Record<string, { status: string; summary: Record<string, any>; error?: string }>;
  estimated_impact: string;
  warnings: string[];
}

interface SuspendTenantDialogProps {
  tenantId: string;
  tenantName: string;
  onClose: () => void;
  onSuspended: () => void;
}

export const SuspendTenantDialog: React.FC<SuspendTenantDialogProps> = ({
  tenantId, tenantName, onClose, onSuspended,
}) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [inventory, setInventory] = useState<InventoryData | null>(null);
  const [confirmName, setConfirmName] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [notes, setNotes] = useState('');
  const [suspending, setSuspending] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const { data } = await client.get(`/api/admin/tenants/${tenantId}/inventory`);
        setInventory(data);
      } catch (e: any) {
        setError(e?.response?.data?.error || e.message || 'Failed to load inventory');
      } finally {
        setLoading(false);
      }
    })();
  }, [tenantId]);

  const handleSuspend = async () => {
    if (confirmName !== tenantName) return;
    setSuspending(true);
    try {
      await client.post(`/api/admin/tenants/${tenantId}/suspend`, {
        deletion_notes: notes,
      });
      onSuspended();
    } catch (e: any) {
      setError(e?.response?.data?.error || e.message || 'Suspension failed');
    } finally {
      setSuspending(false);
    }
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-nkz-surface rounded-lg p-8 max-w-lg w-full mx-4">
          <div className="flex flex-col items-center gap-4">
            <Loader2 className="h-8 w-8 animate-spin text-nkz-accent-base" />
            <p className="text-nkz-text-secondary">{t('admin.inventory_loading')}</p>
          </div>
        </div>
      </div>
    );
  }

  const foundSystems = Object.entries(inventory?.systems || {}).filter(([, s]) => s.status === 'found');
  const foundCount = foundSystems.length;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-y-auto">
      <div className="bg-nkz-surface rounded-lg p-8 max-w-2xl w-full mx-4 my-8">
        <h2 className="text-nkz-xl font-bold text-nkz-text-primary flex items-center gap-2">
          <AlertTriangle className="text-nkz-warning-strong h-6 w-6" />
          {t('admin.suspend_tenant_title', { name: tenantName })}
        </h2>

        {error && (
          <div className="mt-4 p-3 bg-nkz-danger-soft rounded">
            <p className="text-nkz-sm text-nkz-danger">{error}</p>
          </div>
        )}

        <div className="mt-4 p-4 bg-nkz-warning-soft rounded-lg">
          <p className="text-nkz-sm font-medium text-nkz-warning-strong">
            {t('admin.suspend_impact_warning', { count: foundCount })}
          </p>
          <p className="text-nkz-sm text-nkz-text-secondary mt-2">
            {t('admin.suspend_explanation')}
          </p>
        </div>

        <div className="mt-4 max-h-60 overflow-y-auto">
          <table className="w-full text-nkz-sm">
            <thead>
              <tr className="text-nkz-text-secondary border-b border-nkz-border">
                <th className="text-left py-2">{t('admin.system')}</th>
                <th className="text-left py-2">{t('admin.status')}</th>
                <th className="text-left py-2">{t('admin.details')}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(inventory?.systems || {}).map(([name, info]) => (
                <tr key={name} className="border-b border-nkz-border">
                  <td className="py-2 font-mono text-nkz-xs">{name}</td>
                  <td className="py-2">
                    <span className={`text-xs px-2 py-0.5 rounded ${
                      info.status === 'found' ? 'bg-nkz-danger-soft text-nkz-danger' :
                      info.status === 'not_found' ? 'bg-nkz-bg-muted text-nkz-text-muted' :
                      'bg-nkz-warning-soft text-nkz-warning-strong'
                    }`}>
                      {info.status}
                    </span>
                  </td>
                  <td className="py-2 text-nkz-text-secondary text-nkz-xs">
                    {info.status === 'found' ? JSON.stringify(info.summary).substring(0, 80) : info.error || ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {inventory?.warnings && inventory.warnings.length > 0 && (
          <div className="mt-4 p-3 bg-nkz-danger-soft rounded-lg">
            {inventory.warnings.map((w, i) => (
              <p key={i} className="text-nkz-sm text-nkz-danger">{w}</p>
            ))}
          </div>
        )}

        <div className="mt-6 space-y-4">
          <div>
            <label className="text-nkz-sm font-medium text-nkz-text-primary block">
              {t('admin.type_tenant_name_confirm', { name: tenantName })}
            </label>
            <Input
              type="text"
              value={confirmName}
              onChange={(e: any) => setConfirmName(e.target.value)}
              className="mt-1 w-full p-2 border border-nkz-border rounded bg-nkz-bg text-nkz-text-primary"
              placeholder={tenantName}
            />
          </div>

          <label className="flex items-center gap-2 text-nkz-sm">
            <Input type="checkbox" checked={confirmed} onChange={(e: any) => setConfirmed(e.target.checked)} />
            {t('admin.suspend_confirm_checkbox')}
          </label>

          <div>
            <label className="text-nkz-sm font-medium text-nkz-text-primary block">
              {t('admin.suspend_notes_label')}
            </label>
            <textarea
              value={notes}
              onChange={(e: any) => setNotes(e.target.value)}
              className="mt-1 w-full p-2 border border-nkz-border rounded bg-nkz-bg text-nkz-text-primary"
              rows={2}
              placeholder={t('admin.suspend_notes_placeholder')}
            />
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <Button onClick={onClose} className="px-4 py-2 border border-nkz-border rounded text-nkz-text-secondary" disabled={suspending}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleSuspend}
            disabled={confirmName !== tenantName || !confirmed || suspending}
            className="px-4 py-2 bg-nkz-warning-strong text-white rounded disabled:opacity-50"
          >
            {suspending && <Loader2 className="h-4 w-4 animate-spin inline mr-1" />}
            {t('admin.suspend_tenant_button')}
          </Button>
        </div>
      </div>
    </div>
  );
};

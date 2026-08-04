import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Skull } from 'lucide-react';
import client from '@/services/api';
import { PurgeProgressBar } from './PurgeProgressBar';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface PhaseResult {
  phase: string;
  ok: boolean;
  error?: string;
}

interface Props {
  tenantId: string;
  tenantName: string;
  onClose: () => void;
  onPurged: () => void;
}

export const PurgeTenantDialog: React.FC<Props> = ({ tenantId, tenantName, onClose, onPurged }) => {
  const { t } = useTranslation();
  const [step, setStep] = useState<'inventory' | 'confirm' | 'purging' | 'done'>('inventory');
  const [loading, setLoading] = useState(true);
  const [confirmName, setConfirmName] = useState('');
  const [confirmedIrreversible, setConfirmedIrreversible] = useState(false);
  const [phases, setPhases] = useState<PhaseResult[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        await client.get(`/api/admin/tenants/${tenantId}/inventory`);
      } catch (e: any) {
        setError(e?.response?.data?.error || e.message || 'Failed to load inventory');
      } finally {
        setLoading(false);
      }
    })();
  }, [tenantId]);

  const handleStartPurge = async () => {
    if (confirmName !== tenantId || !confirmedIrreversible) return;
    setStep('purging');
    setRunning(true);
    try {
      const { data } = await client.delete(`/api/admin/tenants/${tenantId}/purge`);
      setPhases(data.phases || []);
      setRunning(false);
      setStep('done');
      if (data.status === 'completed') {
        setTimeout(onPurged, 3000);
      }
    } catch (e: any) {
      setPhases(e?.response?.data?.phases || []);
      setRunning(false);
      setStep('done');
      setError(e?.response?.data?.error || 'Purge failed');
    }
  };

  if (loading) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-nkz-surface rounded-lg p-8">
          <Loader2 className="h-8 w-8 animate-spin text-nkz-accent-base mx-auto" />
          <p className="mt-4 text-nkz-text-secondary">{t('admin.inventory_loading')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 overflow-y-auto">
      <div className="bg-nkz-surface rounded-lg p-8 max-w-2xl w-full mx-4 my-8">
        <div className="flex items-center gap-2 mb-6">
          <Skull className="h-6 w-6 text-nkz-danger" />
          <h2 className="text-nkz-xl font-bold text-nkz-danger-strong">
            {t('admin.purge_tenant_title', { name: tenantName })}
          </h2>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-nkz-danger-soft rounded">
            <p className="text-nkz-sm text-nkz-danger-strong">{error}</p>
          </div>
        )}

        {step === 'inventory' && (
          <>
            <div className="p-4 bg-nkz-danger-soft rounded-lg mb-4">
              <p className="text-sm font-bold text-nkz-danger-strong">
                {t('admin.purge_irreversible_warning')}
              </p>
            </div>
            <Button onClick={() => setStep('confirm')}
              className="w-full mt-4 py-2 bg-nkz-danger text-white rounded">
              {t('admin.purge_continue_button')}
            </Button>
          </>
        )}

        {step === 'confirm' && (
          <>
            <div className="p-4 bg-nkz-danger-soft rounded-lg mb-4">
              <p className="text-sm font-bold text-nkz-danger-strong">{t('admin.purge_irreversible_warning')}</p>
              <p className="text-sm text-nkz-danger-strong mt-2">{t('admin.purge_double_confirm')}</p>
            </div>
            <div className="space-y-4">
              <Input type="text" value={confirmName}
                onChange={(e: any) => setConfirmName(e.target.value)}
                className="w-full p-2 border border-nkz-danger rounded bg-nkz-bg text-nkz-text-primary"
                placeholder={t('admin.purge_type_tenant_id', { id: tenantId })} />
              <label className="flex items-center gap-2 text-sm">
                <Input type="checkbox" checked={confirmedIrreversible}
                  onChange={(e: any) => setConfirmedIrreversible(e.target.checked)} />
                {t('admin.purge_confirm_irreversible')}
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-3">
              <Button onClick={() => setStep('inventory')} className="px-4 py-2 border border-nkz-border rounded text-nkz-text-secondary">
                {t('common.back')}
              </Button>
              <Button onClick={handleStartPurge}
                disabled={confirmName !== tenantId || !confirmedIrreversible}
                className="px-4 py-2 bg-nkz-danger text-white rounded disabled:opacity-50">
                {t('admin.purge_execute_button')}
              </Button>
            </div>
          </>
        )}

        {(step === 'purging' || step === 'done') && (
          <>
            <PurgeProgressBar phases={phases} running={running} />
            {step === 'done' && (
              <div className="mt-6 text-center">
                <Button onClick={onClose} className="px-4 py-2 border border-nkz-border rounded text-nkz-text-secondary">
                  {t('common.close')}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

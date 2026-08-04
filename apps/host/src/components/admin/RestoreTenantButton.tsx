import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw, Loader2 } from 'lucide-react';
import client from '@/services/api';
import { Button } from '@nekazari/ui-kit';
import { useToastContext } from '@/context/ToastContext';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Props {
  tenantId: string;
  onRestored: () => void;
}

export const RestoreTenantButton: React.FC<Props> = ({ tenantId, onRestored }) => {
  const { t } = useTranslation();
  const toast = useToastContext();
  const [confirming, setConfirming] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const handleRestore = async () => {
    setRestoring(true);
    try {
      await client.post(`/api/admin/tenants/${tenantId}/restore`);
      onRestored();
    } catch (e: any) {
      toast.error(e?.response?.data?.error || t('admin.restore_failed'));
    } finally {
      setRestoring(false);
      setConfirming(false);
    }
  };

  if (confirming) {
    return (
      <span className="inline-flex items-center gap-2">
        <Button onClick={handleRestore} disabled={restoring}
          className="text-sm px-3 py-1 bg-nkz-success text-white rounded">
          {restoring ? <Loader2 className="h-3 w-3 animate-spin inline mr-1" /> : null}
          {t('admin.confirm_restore')}
        </Button>
        <Button onClick={() => setConfirming(false)}
          className="text-sm px-3 py-1 border border-nkz-border rounded text-nkz-text-secondary">
          {t('cancel')}
        </Button>
      </span>
    );
  }

  return (
    <Button onClick={() => setConfirming(true)}
      className="inline-flex items-center gap-1 text-sm px-3 py-1 border border-nkz-success text-nkz-success rounded hover:bg-nkz-success-soft">
      <RotateCcw className="h-4 w-4" />
      {t('admin.restore_tenant')}
    </Button>
  );
};

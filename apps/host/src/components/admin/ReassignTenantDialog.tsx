import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, UserCheck } from 'lucide-react';
import client from '@/services/api';
import { Button } from '@nekazari/ui-kit';

interface Props {
  userId: string;
  username: string;
  currentTenantId: string;
  onClose: () => void;
  onReassigned: () => void;
}

export const ReassignTenantDialog: React.FC<Props> = ({
  userId, username, currentTenantId, onClose, onReassigned,
}) => {
  const { t } = useTranslation();
  const [tenants, setTenants] = useState<any[]>([]);
  const [selectedTenantId, setSelectedTenantId] = useState('');
  const [loading, setLoading] = useState(false);
  const [reassigning, setReassigning] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const { data } = await client.get('/api/admin/tenants');
        setTenants((data || []).filter((t: any) => !t.deleted_at && t.tenant_id !== currentTenantId));
      } catch { /* ignore */ }
      setLoading(false);
    })();
  }, [currentTenantId]);

  const handleReassign = async () => {
    if (!selectedTenantId) return;
    setReassigning(true);
    try {
      await client.put(`/api/admin/users/${userId}/tenant`, { tenant_id: selectedTenantId });
      onReassigned();
    } catch (e: any) {
      alert(e?.response?.data?.error || 'Reassign failed');
    } finally {
      setReassigning(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-nkz-surface rounded-lg p-6 max-w-md w-full mx-4">
        <h3 className="text-nkz-lg font-bold text-nkz-text-primary flex items-center gap-2">
          <UserCheck className="h-5 w-5" />
          {t('admin.reassign_user_title', { user: username })}
        </h3>
        <p className="text-sm text-nkz-text-secondary mt-2">
          {t('admin.reassign_user_current_tenant', { tenant: currentTenantId })}
        </p>

        {loading ? (
          <Loader2 className="h-6 w-6 animate-spin mx-auto mt-4" />
        ) : (
          <select value={selectedTenantId}
            onChange={(e: any) => setSelectedTenantId(e.target.value)}
            className="mt-4 w-full p-2 border border-nkz-border rounded bg-nkz-bg text-nkz-text-primary">
            <option value="">{t('admin.select_tenant')}</option>
            {tenants.map((t: any) => (
              <option key={t.tenant_id} value={t.tenant_id}>
                {t.tenant_name} ({t.tenant_id})
              </option>
            ))}
          </select>
        )}

        <div className="mt-6 flex justify-end gap-3">
          <Button onClick={onClose} className="px-4 py-2 border border-nkz-border rounded text-nkz-text-secondary">{t('common.cancel')}</Button>
          <Button onClick={handleReassign}
            disabled={!selectedTenantId || reassigning}
            className="px-4 py-2 bg-nkz-accent-base text-white rounded disabled:opacity-50">
            {reassigning ? <Loader2 className="h-4 w-4 animate-spin inline mr-1" /> : null}
            {t('admin.reassign_button')}
          </Button>
        </div>
      </div>
    </div>
  );
};

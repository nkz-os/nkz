// =============================================================================
// Tenant Governance Form - Edit tenant administrative configuration
// =============================================================================
// Allows PlatformAdmin to edit tenant governance (plan_type, contract_end_date, etc.)
// and limits (stored in Orion-LD, separate endpoint)
//
// Key Separation:
// - Governance (PostgreSQL): plan_type, contract_end_date, billing_email, notes
// - Limits (Orion-LD): max_users, max_robots, max_sensors, max_area_hectares

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/context/KeycloakAuthContext';
import { NekazariClient, useTranslation } from '@nekazari/sdk';
import { Card, Button } from '@nekazari/ui-kit';
import { X, Save, RefreshCw, AlertCircle, CheckCircle, Calendar, Mail, FileText, User, Building2 } from 'lucide-react';

interface TenantGovernanceData {
  plan_type: 'basic' | 'pro' | 'premium' | 'enterprise';
  plan_level: number;
  contract_end_date: string | null;
  billing_email: string | null;
  notes: string | null;
  sales_contact: string | null;
  support_level: 'standard' | 'priority' | 'enterprise' | null;
  max_users: number | null;
  max_robots: number | null;
  max_sensors: number | null;
  max_area_hectares: number | null;
}

interface TenantLimits {
  maxUsers: number | null;
  maxRobots: number | null;
  maxSensors: number | null;
  maxAreaHectares: number | null;
}

interface TenantGovernanceFormProps {
  tenantId: string;
  tenantName?: string;
  onClose: () => void;
  onSuccess?: () => void;
}

export const TenantGovernanceForm: React.FC<TenantGovernanceFormProps> = ({
  tenantId,
  tenantName,
  onClose,
  onSuccess
}) => {
  const { getToken, hasRole } = useAuth();
  const { t } = useTranslation('common');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  
  // Unified Governance data (PostgreSQL)
  const [governance, setGovernance] = useState<TenantGovernanceData>({
    plan_type: 'basic',
    plan_level: 0,
    contract_end_date: null,
    billing_email: null,
    notes: null,
    sales_contact: null,
    support_level: 'standard',
    max_users: null,
    max_robots: null,
    max_sensors: null,
    max_area_hectares: null
  });
  
  const [hasChanges, setHasChanges] = useState(false);

  const isPlatformAdmin = hasRole('PlatformAdmin');

  useEffect(() => {
    if (isPlatformAdmin) {
      loadTenantData();
    }
  }, [tenantId, isPlatformAdmin]);

  const loadTenantData = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const client = new NekazariClient({
        baseUrl: '/api',
        getToken: getToken,
        getTenantId: () => tenantId,
      });

      // Load unified governance data from PostgreSQL
      const response = await client.get<{
        tenant_id: string;
        tenant_name: string;
        plan_level: number;
        governance: {
          plan_type: string;
          plan_level: number;
          contract_end_date: string | null;
          billing_email: string | null;
          notes: string | null;
          sales_contact: string | null;
          support_level: string | null;
        };
        limits: {
          maxUsers: number | null;
          maxRobots: number | null;
          maxSensors: number | null;
          maxAreaHectares: number | null;
        };
        plan_type: string;
      }>(`/admin/tenants/${tenantId}/governance`);
      
      if (response) {
        // Set unified data
        setGovernance({
          plan_type: (response.governance.plan_type || response.plan_type || 'basic') as any,
          plan_level: response.plan_level ?? response.governance.plan_level ?? 0,
          contract_end_date: response.governance.contract_end_date 
            ? response.governance.contract_end_date.split('T')[0] 
            : null,
          billing_email: response.governance.billing_email,
          notes: response.governance.notes,
          sales_contact: response.governance.sales_contact,
          support_level: (response.governance.support_level || 'standard') as any,
          max_users: response.limits.maxUsers,
          max_robots: response.limits.maxRobots,
          max_sensors: response.limits.maxSensors,
          max_area_hectares: response.limits.maxAreaHectares,
        });
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load tenant data';
      setError(errorMessage);
      console.error('[TenantGovernanceForm] Error loading data:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveGovernance = async () => {
    if (!isPlatformAdmin) {
      setError('PlatformAdmin permission required');
      return;
    }

    setSaving(true);
    setError(null);
    setSuccess(null);

    try {
      const client = new NekazariClient({
        baseUrl: '/api',
        getToken: getToken,
        getTenantId: () => tenantId,
      });

      // Save everything to PostgreSQL (syncs to Orion-LD in backend)
      await client.put(`/admin/tenants/${tenantId}/governance`, {
        plan_type: governance.plan_type,
        plan_level: governance.plan_level,
        contract_end_date: governance.contract_end_date || null,
        billing_email: governance.billing_email || null,
        notes: governance.notes || null,
        sales_contact: governance.sales_contact || null,
        support_level: governance.support_level || null,
        max_users: governance.max_users,
        max_robots: governance.max_robots,
        max_sensors: governance.max_sensors,
        max_area_hectares: governance.max_area_hectares,
      });

      setSuccess(t('tenant_governance_updated'));
      setHasChanges(false);
      
      setTimeout(() => {
        if (onSuccess) onSuccess();
        onClose();
      }, 1500);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : t('failed_to_save_governance');
      setError(errorMessage);
      console.error('[TenantGovernanceForm] Error saving:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleGovernanceChange = (field: keyof TenantGovernanceData, value: any) => {
    setGovernance(prev => {
      const newData = { ...prev, [field]: value };
      
      // Automatic mapping between type and level
      if (field === 'plan_type') {
        const mapping: Record<string, number> = { 'basic': 0, 'pro': 1, 'premium': 1, 'enterprise': 2 };
        newData.plan_level = mapping[value] ?? 0;
      } else if (field === 'plan_level') {
        const mapping: Record<number, string> = { 0: 'basic', 1: 'pro', 2: 'enterprise' };
        newData.plan_type = (mapping[value] ?? 'basic') as any;
      }
      
      return newData;
    });
    setHasChanges(true);
  };

  if (!isPlatformAdmin) {
    return (
      <Card padding="lg">
        <div className="text-center py-8">
          <AlertCircle className="w-12 h-12 text-red-500 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Access Denied</h2>
          <p className="text-gray-600">{t('platform_admin_required')}</p>
        </div>
      </Card>
    );
  }

  return (
    <div className="fixed inset-0 bg-gray-600 bg-opacity-50 overflow-y-auto h-full w-full z-50 flex items-center justify-center p-4">
      <Card padding="lg" className="max-w-4xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">{t('tenant_governance')}</h2>
            <p className="text-sm text-gray-600 mt-1">
              {tenantName || tenantId}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Messages */}
        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-md flex items-start">
            <AlertCircle className="w-5 h-5 text-red-500 mr-3 mt-0.5" />
            <p className="text-sm text-red-800">{error}</p>
          </div>
        )}

        {success && (
          <div className="mb-4 p-4 bg-green-50 border border-green-200 rounded-md flex items-start">
            <CheckCircle className="w-5 h-5 text-green-500 mr-3 mt-0.5" />
            <p className="text-sm text-green-800">{success}</p>
          </div>
        )}

        {loading ? (
            <div className="text-center py-8">
              <RefreshCw className="w-8 h-8 text-gray-400 mx-auto mb-2 animate-spin" />
              <p className="text-gray-600">{t('loading_tenant_data')}</p>
            </div>
        ) : (
          <div className="space-y-6">
            {/* Section 1: Contract & Administrative Data (PostgreSQL) */}
            <div>
              <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <Building2 className="w-5 h-5 mr-2 text-gray-600" />
                {t('contract_administrative_info')}
              </h3>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Plan Type */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('plan_type')} *
                  </label>
                  <select
                    value={governance.plan_type}
                    onChange={(e) => handleGovernanceChange('plan_type', e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  >
                    <option value="basic">{t('basic')}</option>
                    <option value="pro">{t('pro')}</option>
                    <option value="premium">{t('premium')}</option>
                    <option value="enterprise">{t('enterprise')}</option>
                  </select>
                </div>

                {/* Plan Level (Numeric) */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('plan_level')} (0-2) *
                  </label>
                  <select
                    value={governance.plan_level}
                    onChange={(e) => handleGovernanceChange('plan_level', parseInt(e.target.value))}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  >
                    <option value={0}>0 - Basic</option>
                    <option value={1}>1 - Pro/Premium</option>
                    <option value={2}>2 - Enterprise</option>
                  </select>
                </div>

                {/* Contract End Date */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                    <Calendar className="w-4 h-4 mr-1" />
                    {t('contract_end_date')}
                  </label>
                  <input
                    type="date"
                    value={governance.contract_end_date || ''}
                    onChange={(e) => handleGovernanceChange('contract_end_date', e.target.value || null)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                {/* Billing Email */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                    <Mail className="w-4 h-4 mr-1" />
                    {t('billing_email')}
                  </label>
                  <input
                    type="email"
                    value={governance.billing_email || ''}
                    onChange={(e) => handleGovernanceChange('billing_email', e.target.value || null)}
                    placeholder="billing@example.com"
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                {/* Sales Contact */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                    <User className="w-4 h-4 mr-1" />
                    {t('sales_contact')}
                  </label>
                  <input
                    type="text"
                    value={governance.sales_contact || ''}
                    onChange={(e) => handleGovernanceChange('sales_contact', e.target.value || null)}
                    placeholder="Sales representative name"
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                {/* Support Level */}
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('support_level')}
                  </label>
                  <select
                    value={governance.support_level || 'standard'}
                    onChange={(e) => handleGovernanceChange('support_level', e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  >
                    <option value="standard">{t('standard')}</option>
                    <option value="priority">{t('priority')}</option>
                    <option value="enterprise">{t('enterprise')}</option>
                  </select>
                </div>

                {/* Notes */}
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium text-gray-700 mb-2 flex items-center">
                    <FileText className="w-4 h-4 mr-1" />
                    {t('notes')}
                  </label>
                  <textarea
                    value={governance.notes || ''}
                    onChange={(e) => handleGovernanceChange('notes', e.target.value || null)}
                    placeholder="Internal notes about this tenant..."
                    rows={3}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>
              </div>
            </div>

            {/* Section 2: Quantitative Limits (PostgreSQL Unified) */}
            <div>
              <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center">
                <Building2 className="w-5 h-5 mr-2 text-gray-600" />
                {t('resource_limits')}
              </h3>
              <p className="text-sm text-gray-600 mb-4">
                {t('resource_limits_desc')}
              </p>
              
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('max_users')}
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={governance.max_users ?? ''}
                    onChange={(e) => handleGovernanceChange('max_users', e.target.value === '' ? null : parseInt(e.target.value))}
                    placeholder={t('unlimited')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('max_robots')}
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={governance.max_robots ?? ''}
                    onChange={(e) => handleGovernanceChange('max_robots', e.target.value === '' ? null : parseInt(e.target.value))}
                    placeholder={t('unlimited')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('max_sensors')}
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={governance.max_sensors ?? ''}
                    onChange={(e) => handleGovernanceChange('max_sensors', e.target.value === '' ? null : parseInt(e.target.value))}
                    placeholder={t('unlimited')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {t('max_area_hectares')}
                  </label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={governance.max_area_hectares ?? ''}
                    onChange={(e) => handleGovernanceChange('max_area_hectares', e.target.value === '' ? null : parseFloat(e.target.value))}
                    placeholder={t('unlimited')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                  />
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="flex items-center justify-between pt-4 border-t border-gray-200">
              <div className="text-sm text-gray-600">
                {hasChanges && (
                  <span className="text-yellow-600">{t('unsaved_changes')}</span>
                )}
              </div>
              <div className="flex gap-3">
                <Button
                  variant="secondary"
                  onClick={loadTenantData}
                  disabled={loading || saving}
                >
                  <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
                  {t('reload')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={onClose}
                  disabled={saving}
                >
                  {t('cancel')}
                </Button>
                <Button
                  onClick={handleSaveGovernance}
                  disabled={saving || loading || !hasChanges}
                >
                  <Save className="w-4 h-4 mr-2" />
                  {saving ? t('saving') : t('save_changes')}
                </Button>
              </div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
};

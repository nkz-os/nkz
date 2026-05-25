import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { validateTenantId, type TenantValidationResult } from '@/utils/tenantValidation';
import { getConfig } from '@/config/environment';
import axios from 'axios';
import type { WizardFormData } from './RegistrationWizard';
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

interface StepAccountDetailsProps {
  formData: WizardFormData;
  updateField: (field: keyof WizardFormData, value: string) => void;
  onNext: () => void;
  error: string;
  setError: (msg: string) => void;
  loading: boolean;
}

export const StepAccountDetails: React.FC<StepAccountDetailsProps> = ({
  formData,
  updateField,
  onNext,
  error: _error,
  setError,
  loading,
}) => {
  const { t } = useI18n();
  const config = getConfig();
  const [tenantValidation, setTenantValidation] = useState<TenantValidationResult | null>(null);
  const [checkingTenant, setCheckingTenant] = useState(false);
  const [tenantAvailable, setTenantAvailable] = useState<boolean | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Async tenant name availability check with debounce
  const checkTenantAvailability = useCallback(async (name: string) => {
    if (!name.trim()) {
      setTenantAvailable(null);
      return;
    }
    setCheckingTenant(true);
    try {
      const client = axios.create({
        baseURL: config.api.baseUrl || '',
        timeout: 10000,
        headers: { 'Content-Type': 'application/json' },
      });
      const resp = await client.post('/webhook/register/check-tenant', { name });
      setTenantAvailable(resp.data.available);
    } catch {
      setTenantAvailable(null);
    } finally {
      setCheckingTenant(false);
    }
  }, [config.api.baseUrl]);

  const handleTenantNameChange = useCallback((value: string) => {
    updateField('tenantName', value);
    setTenantAvailable(null);

    if (value.trim()) {
      const validation = validateTenantId(value);
      setTenantValidation(validation);

      // Debounce availability check
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (validation.isValid) {
        debounceRef.current = setTimeout(() => {
          checkTenantAvailability(value);
        }, 500);
      }
    } else {
      setTenantValidation(null);
    }
  }, [updateField, checkTenantAvailability]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!formData.email.trim() || !formData.email.includes('@')) {
      setError(t('activation.email_required'));
      return;
    }
    if (!formData.firstName.trim() || formData.firstName.trim().length < 2) {
      setError(t('registration.first_name_required') || 'First name is required');
      return;
    }
    if (!formData.lastName.trim() || formData.lastName.trim().length < 2) {
      setError(t('registration.last_name_required') || 'Last name is required');
      return;
    }
    if (!formData.tenantName.trim()) {
      setError(t('activation.farm_name_required'));
      return;
    }
    if (tenantValidation && !tenantValidation.isValid) {
      const msg = tenantValidation.errorKey
        ? t(tenantValidation.errorKey, tenantValidation.errorParams as Record<string, unknown> | undefined)
        : t('activation.tenant_name_invalid');
      setError(msg);
      return;
    }

    onNext();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-nkz-lg font-semibold text-nkz-text-primary mb-4">
        {t('registration.step_account') || 'Account Details'}
      </h2>

      {/* Email */}
      <div>
        <label htmlFor="reg-email" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('activation.purchase_email') || 'Email'}
        </label>
        <Input
          id="reg-email"
          type="email"
          value={formData.email}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateField('email', e.target.value)}
          placeholder={t('activation.email_placeholder')}
          required
          autoComplete="email"
        />
      </div>

      {/* First Name */}
      <div>
        <label htmlFor="reg-firstname" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('registration.first_name') || 'First Name'}
        </label>
        <Input
          id="reg-firstname"
          type="text"
          value={formData.firstName}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateField('firstName', e.target.value)}
          placeholder={t('registration.first_name_placeholder') || 'Your first name'}
          required
          autoComplete="given-name"
        />
      </div>

      {/* Last Name */}
      <div>
        <label htmlFor="reg-lastname" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('registration.last_name') || 'Last Name'}
        </label>
        <Input
          id="reg-lastname"
          type="text"
          value={formData.lastName}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateField('lastName', e.target.value)}
          placeholder={t('registration.last_name_placeholder') || 'Your last name'}
          required
          autoComplete="family-name"
        />
      </div>

      {/* Organization Name */}
      <div>
        <label htmlFor="reg-org" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('activation.org_name') || 'Organization Name'}
        </label>
        <Input
          id="reg-org"
          type="text"
          value={formData.tenantName}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => handleTenantNameChange(e.target.value)}
          placeholder={t('activation.org_name_placeholder') || 'Ej. Mi Cooperativa Agrícola'}
          required
          error={tenantValidation !== null && !tenantValidation.isValid}
        />
        {/* Validation feedback */}
        {formData.tenantName && tenantValidation && (
          <div className="mt-1">
            {!tenantValidation.isValid && tenantValidation.errorKey && (
              <p className="text-nkz-xs text-nkz-danger flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {t(tenantValidation.errorKey, tenantValidation.errorParams as Record<string, unknown> | undefined)}
              </p>
            )}
            {tenantValidation.isValid && tenantValidation.warningKey && (
              <p className="text-nkz-xs text-nkz-warning flex items-center gap-1">
                <AlertCircle className="w-3 h-3" />
                {t(tenantValidation.warningKey, tenantValidation.warningParams as Record<string, unknown> | undefined)}
              </p>
            )}
          </div>
        )}
        {/* Availability indicator */}
        {checkingTenant && (
          <p className="text-nkz-xs text-nkz-text-muted flex items-center gap-1 mt-1">
            <Loader2 className="w-3 h-3 animate-spin" />
            {t('registration.org_name_availability_checking') || 'Checking availability...'}
          </p>
        )}
        {!checkingTenant && tenantAvailable === true && (
          <p className="text-nkz-xs text-nkz-success flex items-center gap-1 mt-1">
            <CheckCircle className="w-3 h-3" />
            {t('registration.org_name_available') || 'This name is available'}
          </p>
        )}
        {!checkingTenant && tenantAvailable === false && (
          <p className="text-nkz-xs text-nkz-danger flex items-center gap-1 mt-1">
            <AlertCircle className="w-3 h-3" />
            {t('registration.org_name_taken') || 'This organization name is already registered'}
          </p>
        )}
        {tenantValidation?.normalized && (
          <div className="mt-1">
            <span className="text-nkz-xs text-nkz-text-muted">ID: </span>
            <span className="text-nkz-xs font-mono text-nkz-text-secondary">{tenantValidation.normalized}</span>
          </div>
        )}
      </div>

      {/* Submit */}
      <div className="pt-2">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          disabled={loading}
          loading={loading}
        >
          {t('registration.continue') || 'Continue'}
        </Button>
      </div>
    </form>
  );
};

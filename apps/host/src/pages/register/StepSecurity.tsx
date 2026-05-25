import React, { useState, useMemo } from 'react';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { TermsAcceptance } from '@/components/TermsAcceptance';
import type { WizardFormData } from './RegistrationWizard';
import { Eye, EyeOff, Check, Minus } from 'lucide-react';

interface StepSecurityProps {
  formData: WizardFormData;
  updateField: (field: keyof WizardFormData, value: string) => void;
  onSubmit: () => void;
  onBack: () => void;
  error: string;
  loading: boolean;
}

interface PasswordRequirements {
  minLength: boolean;
  hasDigit: boolean;
  hasLowercase: boolean;
  hasUppercase: boolean;
  hasSpecialChar: boolean;
}

export const StepSecurity: React.FC<StepSecurityProps> = ({
  formData,
  updateField,
  onSubmit,
  onBack,
  error,
  loading,
}) => {
  const { t } = useI18n();
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [localError, setLocalError] = useState('');

  const requirements: PasswordRequirements = useMemo(() => ({
    minLength: formData.password.length >= 8,
    hasDigit: /\d/.test(formData.password),
    hasLowercase: /[a-z]/.test(formData.password),
    hasUppercase: /[A-Z]/.test(formData.password),
    hasSpecialChar: /[\]!@#$%^&*()_+\-=[{};':"\\|,.<>/?]/.test(formData.password),
  }), [formData.password]);

  const isPasswordValid = Object.values(requirements).every(Boolean);

  const requirementItems = [
    { met: requirements.minLength, label: t('activation.password_requirement_min_length') || 'Minimum 8 characters' },
    { met: requirements.hasDigit, label: t('activation.password_requirement_digit') || 'At least one number' },
    { met: requirements.hasLowercase, label: t('activation.password_requirement_lowercase') || 'At least one lowercase letter' },
    { met: requirements.hasUppercase, label: t('activation.password_requirement_uppercase') || 'At least one uppercase letter' },
    { met: requirements.hasSpecialChar, label: t('activation.password_requirement_special') || 'At least one special character' },
  ];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setLocalError('');

    if (!isPasswordValid) {
      setLocalError(t('activation.password_min_length') || 'Password must meet all requirements');
      return;
    }
    if (formData.password !== formData.confirmPassword) {
      setLocalError(t('activation.passwords_mismatch') || 'Passwords do not match');
      return;
    }
    if (!termsAccepted) {
      setLocalError(t('terms.required'));
      return;
    }

    onSubmit();
  };

  const displayError = localError || error;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-nkz-lg font-semibold text-nkz-text-primary">
          {t('registration.step_security') || 'Security'}
        </h2>
        <button
          type="button"
          onClick={onBack}
          className="text-nkz-sm text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
        >
          {t('registration.back') || 'Back'}
        </button>
      </div>

      {displayError && (
        <div className="p-2 bg-nkz-danger-soft border border-nkz-danger rounded-nkz-md">
          <p className="text-nkz-xs text-nkz-danger">{displayError}</p>
        </div>
      )}

      {/* Password */}
      <div>
        <label htmlFor="reg-password" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('activation.password') || 'Password'}
        </label>
        <div className="relative">
          <Input
            id="reg-password"
            type={showPassword ? 'text' : 'password'}
            value={formData.password}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateField('password', e.target.value)}
            placeholder={t('activation.password_placeholder') || '••••••••'}
            required
            autoComplete="new-password"
            className="pr-10"
          />
          <button
            type="button"
            onClick={() => setShowPassword(!showPassword)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-nkz-text-muted hover:text-nkz-text-primary p-1"
            tabIndex={-1}
          >
            {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Password Requirements Checklist */}
      {formData.password.length > 0 && (
        <div className="bg-nkz-surface-sunken rounded-nkz-md p-3 space-y-1">
          <p className="text-nkz-xs text-nkz-text-secondary font-medium mb-1">
            {t('activation.password_requirements_title') || 'Password must contain:'}
          </p>
          {requirementItems.map((item, idx) => (
            <div key={idx} className="flex items-center gap-1.5">
              {item.met ? (
                <Check className="w-3.5 h-3.5 text-nkz-success flex-shrink-0" />
              ) : (
                <Minus className="w-3.5 h-3.5 text-nkz-text-muted flex-shrink-0" />
              )}
              <span className={`text-nkz-xs ${item.met ? 'text-nkz-success' : 'text-nkz-text-muted'}`}>
                {item.label}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Confirm Password */}
      <div>
        <label htmlFor="reg-confirm" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
          {t('activation.confirm_password') || 'Confirm Password'}
        </label>
        <div className="relative">
          <Input
            id="reg-confirm"
            type={showConfirm ? 'text' : 'password'}
            value={formData.confirmPassword}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => updateField('confirmPassword', e.target.value)}
            placeholder={t('activation.password_placeholder') || '••••••••'}
            required
            autoComplete="new-password"
            error={formData.confirmPassword.length > 0 && formData.password !== formData.confirmPassword}
            className="pr-10"
          />
          <button
            type="button"
            onClick={() => setShowConfirm(!showConfirm)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-nkz-text-muted hover:text-nkz-text-primary p-1"
            tabIndex={-1}
          >
            {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {formData.confirmPassword.length > 0 && formData.password !== formData.confirmPassword && (
          <p className="text-nkz-xs text-nkz-danger mt-1">
            {t('activation.passwords_mismatch') || 'Passwords do not match'}
          </p>
        )}
      </div>

      {/* Terms Acceptance */}
      <div className="pt-1">
        <TermsAcceptance onAcceptChange={setTermsAccepted} required />
      </div>

      {/* Submit */}
      <div className="pt-2">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          disabled={loading || !isPasswordValid || !termsAccepted}
          loading={loading}
        >
          {t('registration.confirm_otp') || 'Create Account'}
        </Button>
      </div>
    </form>
  );
};

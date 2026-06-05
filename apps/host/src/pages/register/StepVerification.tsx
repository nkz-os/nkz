import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { getConfig } from '@/config/environment';
import axios from 'axios';
import type { WizardFormData } from './RegistrationWizard';

/* eslint-disable @typescript-eslint/no-explicit-any */
interface StepVerificationProps {
  formData: WizardFormData;
  updateField: (field: keyof WizardFormData, value: string) => void;
  verificationMethod: 'otp' | 'code';
  setVerificationMethod: (method: 'otp' | 'code') => void;
  onNext: () => void;
  onBack: () => void;
  error: string;
  setError: (msg: string) => void;
  loading: boolean;
  setLoading: (v: boolean) => void;
}

export const StepVerification: React.FC<StepVerificationProps> = ({
  formData,
  updateField,
  verificationMethod,
  setVerificationMethod,
  onNext,
  onBack,
  error: _error,
  setError,
  loading,
  setLoading,
}) => {
  const { t } = useI18n();
  const config = getConfig();
  const [otpSent, setOtpSent] = useState(false);
  const [otpMessage, setOtpMessage] = useState('');
  const [resendCountdown, setResendCountdown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (resendCountdown > 0) {
      timerRef.current = setInterval(() => {
        setResendCountdown(prev => {
          if (prev <= 1) {
            if (timerRef.current) clearInterval(timerRef.current);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [resendCountdown]);

  const handleSendOtp = useCallback(async () => {
    setLoading(true);
    setError('');
    setOtpMessage('');
    try {
      const client = axios.create({
        baseURL: config.api.baseUrl || '',
        timeout: 30000,
        headers: { 'Content-Type': 'application/json' },
      });
      const resp = await client.post('/webhook/register/request-otp', {
        email: formData.email.toLowerCase(),
      });
      if (resp.data.success) {
        setOtpSent(true);
        setOtpMessage(resp.data.message || (t('registration.send_otp_success') || 'Code sent'));
        setResendCountdown(60);
      }
    } catch (err: unknown) {
      const error = err as { response?: { status?: number } };
      if (error.response?.status === 429) {
        setError(t('registration.rate_limit') || 'Too many attempts. Please try again later.');
      } else {
        setError(t('registration.otp_send_error') || 'Error sending verification code.');
      }
    } finally {
      setLoading(false);
    }
  }, [formData.email, config.api.baseUrl, setLoading, setError, t]);

  const formatCode = (code: string) => {
    const cleaned = code.replace(/[^A-Z0-9]/g, '').toUpperCase();
    let codePart = cleaned.startsWith('NEK') ? cleaned.slice(3) : cleaned;
    codePart = codePart.slice(0, 12);
    if (codePart.length === 0) return '';
    if (codePart.length <= 4) return `NEK-${codePart}`;
    if (codePart.length <= 8) return `NEK-${codePart.slice(0, 4)}-${codePart.slice(4)}`;
    return `NEK-${codePart.slice(0, 4)}-${codePart.slice(4, 8)}-${codePart.slice(8, 12)}`;
  };

  const handleCodeChange = (value: string) => {
    updateField('activationCode', formatCode(value));
  };

  const handleOtpChange = (value: string) => {
    const digits = value.replace(/\D/g, '').slice(0, 6);
    updateField('otp', digits);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (verificationMethod === 'code') {
      if (!formData.activationCode.trim() || formData.activationCode.length < 7) {
        setError(t('activation.code_required'));
        return;
      }
    } else {
      if (formData.otp.length !== 6) {
        setError(t('registration.otp_incomplete') || 'Please enter the 6-digit code');
        return;
      }
    }

    onNext();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-nkz-lg font-semibold text-nkz-text-primary">
          {t('registration.step_verify') || 'Verification'}
        </h2>
        <Button
          type="button"
          onClick={onBack}
          className="text-nkz-sm text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
        >
          {t('registration.back') || 'Back'}
        </Button>
      </div>

      {/* Method selector */}
      <div className="space-y-2 mb-4">
        <p className="text-nkz-sm text-nkz-text-secondary font-medium">
          {t('registration.verify_method_title') || 'How would you like to verify?'}
        </p>

        <label className="flex items-center gap-3 p-3 border border-nkz-border rounded-nkz-md cursor-pointer hover:bg-nkz-surface-sunken transition-colors">
          <input
            type="radio"
            name="verifyMethod"
            checked={verificationMethod === 'code'}
            onChange={() => setVerificationMethod('code')}
            className="text-nkz-accent-base focus:ring-nkz-accent-base"
          />
          <span className="text-nkz-sm text-nkz-text-primary">
            {t('registration.verify_method_code') || 'I have an activation code'}
          </span>
        </label>

        <label className="flex items-center gap-3 p-3 border border-nkz-border rounded-nkz-md cursor-pointer hover:bg-nkz-surface-sunken transition-colors">
          <input
            type="radio"
            name="verifyMethod"
            checked={verificationMethod === 'otp'}
            onChange={() => setVerificationMethod('otp')}
            className="text-nkz-accent-base focus:ring-nkz-accent-base"
          />
          <span className="text-nkz-sm text-nkz-text-primary">
            {t('registration.verify_method_email') || 'Send me a verification code'}
          </span>
        </label>
      </div>

      {/* Activation Code Input */}
      {verificationMethod === 'code' && (
        <div>
          <label htmlFor="reg-code" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1">
            {t('activation.activation_code')}
          </label>
          <Input
            id="reg-code"
            type="text"
            value={formData.activationCode}
            onChange={(e: any) => handleCodeChange(e.target.value)}
            placeholder={t('activation.activation_code_placeholder')}
            className="font-mono text-center tracking-wider"
            maxLength={18}
            required
          />
        </div>
      )}

      {/* OTP Section */}
      {verificationMethod === 'otp' && (
        <div className="space-y-3">
          <div className="bg-nkz-surface-sunken rounded-nkz-md p-3 text-center">
            <p className="text-nkz-sm text-nkz-text-secondary">
              {t('registration.otp_message') || 'We will send a 6-digit code to'}{' '}
              <strong className="text-nkz-text-primary">{formData.email}</strong>
            </p>
          </div>

          {!otpSent ? (
            <Button
              type="button"
              variant="secondary"
              size="lg"
              className="w-full"
              onClick={handleSendOtp}
              disabled={loading}
              loading={loading}
            >
              {t('registration.send_otp') || 'Send Verification Code'}
            </Button>
          ) : (
            <>
              {otpMessage && (
                <p className="text-nkz-xs text-nkz-success text-center">{otpMessage}</p>
              )}
              <div>
                <label htmlFor="reg-otp" className="block text-nkz-sm text-nkz-text-secondary font-medium mb-1 text-center">
                  {t('registration.otp_label') || 'Verification code'}
                </label>
                <Input
                  id="reg-otp"
                  type="text"
                  value={formData.otp}
                  onChange={(e: any) => handleOtpChange(e.target.value)}
                  placeholder="000000"
                  className="font-mono text-center text-nkz-xl tracking-[0.25em]"
                  maxLength={6}
                  required
                />
              </div>
              <div className="text-center">
                {resendCountdown > 0 ? (
                  <span className="text-nkz-xs text-nkz-text-muted">
                    {t('registration.resend_otp', { seconds: resendCountdown }) || `Resend code in ${resendCountdown}s`}
                  </span>
                ) : (
                  <Button
                    type="button"
                    onClick={handleSendOtp}
                    className="text-nkz-xs text-nkz-accent-base hover:text-nkz-accent-strong font-medium"
                  >
                    {t('registration.resend_otp_available') || 'Resend code'}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* Submit */}
      <div className="pt-2">
        <Button
          type="submit"
          variant="primary"
          size="lg"
          className="w-full"
          disabled={loading || (verificationMethod === 'otp' && formData.otp.length !== 6)}
          loading={loading}
        >
          {verificationMethod === 'code'
            ? t('activation.activate_button') || 'Activate Account'
            : t('registration.continue') || 'Continue'}
        </Button>
      </div>
    </form>
  );
};

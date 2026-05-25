import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/KeycloakAuthContext';
import { useI18n } from '@/context/I18nContext';
import { StepIndicator } from './StepIndicator';
import { StepAccountDetails } from './StepAccountDetails';
import { StepVerification } from './StepVerification';
import { StepSecurity } from './StepSecurity';
import { StepSuccess } from './StepSuccess';
import { Card } from '@nekazari/ui-kit';
import axios from 'axios';
import { getConfig } from '@/config/environment';

export interface WizardFormData {
  email: string;
  firstName: string;
  lastName: string;
  tenantName: string;
  password: string;
  confirmPassword: string;
  activationCode: string;
  otp: string;
}

export type WizardStep = 1 | 2 | 3 | 4;

export interface ActivationResponse {
  success: boolean;
  tenant_id: string;
  namespace: string;
  api_key: string;
  plan: string;
  limits: {
    max_users: number;
    max_robots: number;
    max_sensors: number;
  };
  expires_at: string;
  keycloak_user_created: boolean;
}

interface RegistrationWizardProps {
  defaultMethod?: 'otp' | 'code';
}

export const RegistrationWizard: React.FC<RegistrationWizardProps> = ({
  defaultMethod = 'otp',
}) => {
  const { login } = useAuth();
  const { t } = useI18n();
  const navigate = useNavigate();
  const config = getConfig();

  const [step, setStep] = useState<WizardStep>(1);
  const [verificationMethod, setVerificationMethod] = useState<'otp' | 'code'>(defaultMethod);
  const [formData, setFormData] = useState<WizardFormData>({
    email: '',
    firstName: '',
    lastName: '',
    tenantName: '',
    password: '',
    confirmPassword: '',
    activationCode: '',
    otp: '',
  });
  const [error, setError] = useState('');
  const [errorDetail, setErrorDetail] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('');
  const [success, setSuccess] = useState<ActivationResponse | null>(null);

  const updateField = useCallback((field: keyof WizardFormData, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    setError('');
    setErrorDetail('');
  }, []);

  const handleStep1Next = useCallback(() => {
    setStep(2);
  }, []);

  const handleStep2Next = () => {
    if (verificationMethod === 'code') {
      // Activation code path: submit immediately
      handleActivationSubmit();
    } else {
      // OTP path: go to security step
      setStep(3);
    }
  };

  const handleStep3Submit = async () => {
    await handleOtpSubmit();
  };

  const handleActivationSubmit = async () => {
    setLoading(true);
    setError('');
    setErrorDetail('');
    setLoadingMessage(t('activation.creating_tenant') || 'Creando cuenta...');

    try {
      let codeToSend = formData.activationCode.toUpperCase().trim();
      codeToSend = codeToSend.replace(/\s+/g, '').replace(/-+/g, '-');
      if (!codeToSend.startsWith('NEK-')) {
        const cleaned = codeToSend.replace(/[^A-Z0-9]/g, '').slice(0, 12);
        if (cleaned.length >= 4) {
          codeToSend = `NEK-${cleaned.slice(0, 4)}-${cleaned.slice(4, 8)}-${cleaned.slice(8, 12)}`;
        } else {
          codeToSend = `NEK-${cleaned}`;
        }
      }

      const client = axios.create({
        baseURL: config.api.baseUrl || '',
        timeout: 120000,
        headers: { 'Content-Type': 'application/json' },
      });

      const response = await client.post('/webhook/activate', {
        code: codeToSend,
        email: formData.email.toLowerCase(),
        tenant_name: formData.tenantName,
        password: formData.password,
        first_name: formData.firstName,
        last_name: formData.lastName,
      });

      if (response.data.success) {
        setSuccess(response.data);
        setStep(4);
        setTimeout(() => login(), 3000);
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string; reason?: string } }; message?: string };
      const msg = error.response?.data?.error || error.message || t('activation.error_activating');
      setError(msg);
      setErrorDetail(error.response?.data?.reason || '');
      if (msg.includes('Invalid or expired')) {
        setError(t('activation.invalid_code'));
      }
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  const handleOtpSubmit = async () => {
    setLoading(true);
    setError('');
    setErrorDetail('');
    setLoadingMessage(t('registration.creating') || 'Creando cuenta...');

    try {
      const client = axios.create({
        baseURL: config.api.baseUrl || '',
        timeout: 120000,
        headers: { 'Content-Type': 'application/json' },
      });

      const response = await client.post('/webhook/register', {
        email: formData.email.toLowerCase(),
        organization_name: formData.tenantName,
        password: formData.password,
        first_name: formData.firstName,
        last_name: formData.lastName,
        otp: formData.otp,
      });

      if (response.data.success) {
        setSuccess(response.data);
        setStep(4);
        setTimeout(() => {
          navigate('/login', {
            state: { message: t('registration.account_created') || 'Cuenta creada con éxito. Ya puedes iniciar sesión.' },
          });
        }, 3000);
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string }; status?: number }; message?: string };
      const msg = error.response?.data?.error || error.message || 'Registration failed';
      setError(msg);
      if (error.response?.status === 409) {
        setError(t('registration.email_exists') || 'El email ya está registrado');
      } else if (error.response?.status === 401) {
        setError(t('registration.otp_invalid') || 'Código de verificación inválido o expirado.');
      }
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  };

  const handleBack = useCallback(() => {
    if (step > 1) {
      setStep((prev) => (prev - 1) as WizardStep);
      setError('');
      setErrorDetail('');
    }
  }, [step]);

  return (
    <div className="min-h-screen bg-nkz-surface-sunken flex flex-col items-center justify-center p-4">
      <div className="max-w-lg w-full">
        {/* Header */}
        <div className="text-center mb-6">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-nkz-accent-base rounded-full mb-3">
            <span className="text-white text-xl font-bold">NKZ</span>
          </div>
          <h1 className="text-nkz-2xl font-bold text-nkz-text-primary">
            {defaultMethod === 'code'
              ? t('activation.title') || 'Activar tu Cuenta'
              : t('activation.register_title') || 'Prueba 45 días Gratis'}
          </h1>
          <p className="text-nkz-sm text-nkz-text-secondary mt-2">
            {defaultMethod === 'code'
              ? t('activation.subtitle') || 'Introduce tu código de activación'
              : t('activation.register_subtitle') || 'Únete a Nekazari y digitaliza tu explotación hoy mismo.'}
          </p>
        </div>

        {/* Step Indicator */}
        <div className="mb-6">
          <StepIndicator currentStep={step} />
        </div>

        {/* Error Display */}
        {error && (
          <div className="mb-4 p-3 bg-nkz-danger-soft border border-nkz-danger rounded-nkz-md">
            <p className="text-nkz-sm font-medium text-nkz-danger">{error}</p>
            {errorDetail && (
              <p className="text-nkz-xs text-nkz-text-secondary mt-1">{errorDetail}</p>
            )}
          </div>
        )}

        {/* Loading Display */}
        {loading && (
          <div className="mb-4 p-3 bg-nkz-surface-sunken border border-nkz-border rounded-nkz-md flex items-center gap-2">
            <span className="animate-spin text-nkz-accent-base">{'⟳'}</span>
            <p className="text-nkz-sm text-nkz-text-primary">
              {loadingMessage || t('activation.please_wait') || 'Procesando...'}
            </p>
          </div>
        )}

        {/* Step Content */}
        <Card padding="lg" className="shadow-nkz-md">
          {step === 1 && (
            <StepAccountDetails
              formData={formData}
              updateField={updateField}
              onNext={handleStep1Next}
              error={error}
              setError={setError}
              loading={loading}
            />
          )}
          {step === 2 && (
            <StepVerification
              formData={formData}
              updateField={updateField}
              verificationMethod={verificationMethod}
              setVerificationMethod={setVerificationMethod}
              onNext={handleStep2Next}
              onBack={handleBack}
              error={error}
              setError={setError}
              loading={loading}
              setLoading={setLoading}
            />
          )}
          {step === 3 && (
            <StepSecurity
              formData={formData}
              updateField={updateField}
              onSubmit={handleStep3Submit}
              onBack={handleBack}
              error={error}
              loading={loading}
            />
          )}
          {step === 4 && success && (
            <StepSuccess
              success={success}
              isCodePath={verificationMethod === 'code'}
              onLogin={login}
            />
          )}
        </Card>

        {/* Footer */}
        <div className="mt-6 text-center">
          <p className="text-nkz-xs text-nkz-text-muted">
            {defaultMethod === 'code'
              ? t('activation.no_code') || "Don't have an activation code?"
              : t('activation.already_have_code') || '¿Ya tienes un código?'}{' '}
            <a
              href={defaultMethod === 'code' ? '/register' : '/activate'}
              className="text-nkz-accent-base hover:text-nkz-accent-strong font-medium"
            >
              {defaultMethod === 'code'
                ? t('activation.register_button') || 'Registrarse'
                : t('activation.activate_code_link') || 'Activar Código'}
            </a>
          </p>
        </div>
      </div>
    </div>
  );
};

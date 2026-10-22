import React, { useState, useEffect } from 'react';
import { Button } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import type { ActivationResponse } from './RegistrationWizard';
import { CheckCircle } from 'lucide-react';

interface StepSuccessProps {
  success: ActivationResponse;
  isCodePath: boolean;
  onLogin: () => void;
}

export const StepSuccess: React.FC<StepSuccessProps> = ({ success, isCodePath, onLogin }) => {
  const { t } = useI18n();
  const [countdown, setCountdown] = useState(3);

  useEffect(() => {
    const timer = setInterval(() => {
      setCountdown(prev => prev - 1);
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (countdown <= 0) {
      if (isCodePath) {
        onLogin();
      }
    }
  }, [countdown, isCodePath, onLogin]);

  return (
    <div className="text-center space-y-5">
      <div className="flex justify-center">
        <CheckCircle className="w-16 h-16 text-nkz-success" />
      </div>

      <div>
        <h2 className="text-nkz-xl font-bold text-nkz-text-primary mb-2">
          {t('activation.register_success_title') || 'Welcome to Nekazari!'}
        </h2>
        <p className="text-nkz-sm text-nkz-text-secondary">
          {t('activation.register_success_message') || 'Your account has been created successfully.'}
        </p>
      </div>

      {/* Account Details */}
      <div className="bg-nkz-success-soft border border-nkz-success rounded-nkz-md p-4 text-left">
        <h3 className="text-nkz-sm font-semibold text-nkz-text-primary mb-3">
          {t('activation.account_details') || 'Your Account Details'}
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="text-nkz-xs text-nkz-text-muted">
              {t('activation.org_id') || 'Organization ID'}
            </p>
            <p className="text-nkz-sm font-mono text-nkz-text-primary">{success.tenant_id}</p>
          </div>
          <div>
            <p className="text-nkz-xs text-nkz-text-muted">{t('activation.plan') || 'Plan'}</p>
            <p className="text-nkz-sm font-medium text-nkz-text-primary capitalize">{success.plan}</p>
          </div>
          {success.api_key && (
            <div className="col-span-2">
              <p className="text-nkz-xs text-nkz-text-muted">{t('activation.api_key') || 'API Key'}</p>
              <p className="text-nkz-xs font-mono text-nkz-text-secondary break-all">{success.api_key}</p>
            </div>
          )}
          {success.expires_at && (
            <div>
              <p className="text-nkz-xs text-nkz-text-muted">{t('activation.expires') || 'Expires'}</p>
              <p className="text-nkz-sm text-nkz-text-primary">
                {new Date(success.expires_at).toLocaleDateString()}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Redirect message */}
      <div>
        <p className="text-nkz-xs text-nkz-text-muted mb-3">
          {t('registration.redirect_login', { seconds: countdown }) ||
            `You will be redirected in ${countdown} seconds...`}
        </p>
        {isCodePath ? (
          <Button variant="primary" size="lg" className="w-full" onClick={onLogin}>
            {t('activation.go_to_dashboard') || 'Go to Dashboard'}
          </Button>
        ) : (
          <Button variant="primary" size="lg" className="w-full" onClick={() => window.location.href = '/login'}>
            {t('registration.go_to_login') || 'Go to Login'}
          </Button>
        )}
      </div>
    </div>
  );
};

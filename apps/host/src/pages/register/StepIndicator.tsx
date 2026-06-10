import React from 'react';
import clsx from 'clsx';
import { useI18n } from '@/context/I18nContext';
import type { WizardStep } from './RegistrationWizard';
import { Check } from 'lucide-react';

interface StepIndicatorProps {
  currentStep: WizardStep;
}

const steps: { step: WizardStep; labelKey: string; defaultLabel: string }[] = [
  { step: 1, labelKey: 'registration.step_account', defaultLabel: 'Account' },
  { step: 2, labelKey: 'registration.step_verify', defaultLabel: 'Verify' },
  { step: 3, labelKey: 'registration.step_security', defaultLabel: 'Security' },
  { step: 4, labelKey: 'registration.step_complete', defaultLabel: 'Complete' },
];

export const StepIndicator: React.FC<StepIndicatorProps> = ({ currentStep }) => {
  const { t } = useI18n();

  return (
    <nav aria-label="Progress" className="flex items-center justify-between">
      {steps.map((s, idx) => {
        const isActive = s.step === currentStep;
        const isCompleted = s.step < currentStep;
        const isPending = s.step > currentStep;

        const circleClasses = clsx(
          'flex items-center justify-center w-8 h-8 rounded-full text-nkz-xs font-bold transition-colors duration-nkz-fast',
          isActive && 'bg-nkz-accent-base text-nkz-text-on-accent',
          isCompleted && 'bg-nkz-success text-white',
          isPending && 'bg-nkz-surface-sunken text-nkz-text-muted border border-nkz-border',
        );

        const labelClasses = clsx(
          'text-nkz-xs mt-1 text-center whitespace-nowrap',
          isActive && 'text-nkz-accent-base font-medium',
          isCompleted && 'text-nkz-success',
          isPending && 'text-nkz-text-muted',
        );

        return (
          <React.Fragment key={s.step}>
            <div className="flex flex-col items-center">
              <span className={circleClasses}>
                {isCompleted ? <Check className="w-4 h-4" /> : s.step}
              </span>
              <span className={labelClasses}>
                {t(s.labelKey, { defaultValue: s.defaultLabel })}
              </span>
            </div>
            {idx < steps.length - 1 && (
              <div
                className={clsx(
                  'flex-1 h-0.5 mx-2 transition-colors duration-nkz-fast',
                  s.step < currentStep ? 'bg-nkz-success' : 'bg-nkz-border',
                )}
              />
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
};

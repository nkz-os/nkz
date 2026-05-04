import React from 'react';
import { useI18n } from '@/context/I18nContext';

const METRICS = [
  { value: '127', key: 'landing_v2.proof_parcelas' },
  { value: '∞', key: 'landing_v2.proof_hardware' },
  { value: '3.2M', key: 'landing_v2.proof_observaciones' },
  { value: '6', key: 'landing_v2.proof_paises' },
];

export const ProofBand: React.FC = () => {
  const { t } = useI18n();

  return (
    <section
      className="bg-[#FAFAF7]"
      style={{
        padding: '4rem 2rem',
        borderTop: '1px solid rgba(14,26,20,0.08)',
        borderBottom: '1px solid rgba(14,26,20,0.08)',
      }}
    >
      <div className="max-w-[1200px] mx-auto grid grid-cols-2 md:grid-cols-4 gap-8">
        {METRICS.map((m) => (
          <div key={m.key} className="text-center">
            <div
              className="font-mono-landing font-medium text-[#0E1A14] mb-1"
              style={{ fontSize: 'clamp(2rem, 3.5vw, 3rem)' }}
            >
              {m.value}
            </div>
            <div className="text-sm text-[#5B6660] leading-tight">{t(m.key)}</div>
          </div>
        ))}
      </div>
    </section>
  );
};

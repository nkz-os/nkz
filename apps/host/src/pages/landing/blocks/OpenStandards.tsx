import React from 'react';
import { useI18n } from '@/context/I18nContext';

const STANDARDS = 'FIWARE NGSI-LD · Smart Data Models · Keycloak (OIDC) · Kubernetes · TimescaleDB · MQTT · OAuth 2.0 · MinIO (S3)';

export const OpenStandards: React.FC = () => {
  const { t } = useI18n();

  return (
    <section className="bg-[#FAFAF7]" style={{ padding: '4rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto text-center">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
          {t('landing_v2.standards_eyebrow')}
        </p>
        <h2
          className="text-[#0E1A14] font-semibold mb-8"
          style={{ fontSize: '28px', letterSpacing: '-0.02em' }}
        >
          {t('landing_v2.standards_h2')}
        </h2>
        <p className="font-mono-landing text-lg text-[#5B6660] leading-relaxed max-w-[48ch] mx-auto">
          {STANDARDS}
        </p>
      </div>
    </section>
  );
};

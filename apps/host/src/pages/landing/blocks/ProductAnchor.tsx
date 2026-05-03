import React from 'react';
import { useI18n } from '@/context/I18nContext';

export const ProductAnchor: React.FC = () => {
  const { t } = useI18n();

  const bullets = [
    'landing_v2.product_bullet_1',
    'landing_v2.product_bullet_2',
    'landing_v2.product_bullet_3',
    'landing_v2.product_bullet_4',
  ];

  return (
    <section className="bg-white" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto grid lg:grid-cols-2 gap-16 items-center">
        <div>
          <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
            {t('landing_v2.product_eyebrow')}
          </p>
          <h2
            className="text-[#0E1A14] font-semibold mb-6"
            style={{
              fontFamily: "'Inter', sans-serif",
              fontSize: 'clamp(2rem, 4vw, 3rem)',
              lineHeight: 1.15,
              letterSpacing: '-0.02em',
            }}
          >
            {t('landing_v2.product_h2')}
          </h2>
          <p className="text-[#5B6660] text-base leading-relaxed mb-6 max-w-[48ch]">
            {t('landing_v2.product_body')}
          </p>
          <ul className="space-y-3">
            {bullets.map((b) => (
              <li key={b} className="flex items-start gap-2 text-[#0E1A14] text-sm">
                <span className="text-[#1F4D38] font-medium mt-0.5">&middot;</span>
                {t(b)}
              </li>
            ))}
          </ul>
        </div>
        <div className="relative">
          <div
            className="overflow-hidden bg-[#FAFAF7] flex items-center justify-center"
            style={{
              borderRadius: '12px',
              boxShadow: '0 30px 60px -20px rgba(14,26,20,0.25), 0 0 0 1px rgba(14,26,20,0.06)',
              aspectRatio: '16/9',
            }}
          >
            <img
              src="/media/cesium-viewer.png"
              alt="Nekazari Cesium viewer"
              className="w-full h-full object-cover"
              loading="lazy"
            />
          </div>
        </div>
      </div>
    </section>
  );
};

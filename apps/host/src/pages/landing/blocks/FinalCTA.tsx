import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';

export const FinalCTA: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="bg-[#0E1A14]" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto text-center">
        <h2
          className="text-white font-semibold mb-8"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2rem, 5vw, 3rem)',
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
          }}
        >
          {t('landing_v2.cta_final_h2')}
        </h2>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <button
            onClick={() => navigate('/register')}
            className="inline-flex items-center px-7 py-3.5 bg-white text-[#0E1A14] font-semibold rounded-lg hover:-translate-y-px transition-all duration-200"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.cta_final_primary')}
          </button>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'info@nekazari.com'}`}
            className="inline-flex items-center gap-1.5 text-white/70 font-medium hover:text-white transition-colors group"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.cta_final_secondary')}
            <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
          </a>
        </div>
      </div>
    </section>
  );
};

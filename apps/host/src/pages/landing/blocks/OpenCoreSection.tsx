import React from 'react';
import { ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '@/context/I18nContext';

const ossItems = [
  'landing_v2.opencore_oss_1',
  'landing_v2.opencore_oss_2',
  'landing_v2.opencore_oss_3',
  'landing_v2.opencore_oss_4',
  'landing_v2.opencore_oss_5',
];

const cloudItems = [
  'landing_v2.opencore_cloud_1',
  'landing_v2.opencore_cloud_2',
  'landing_v2.opencore_cloud_3',
  'landing_v2.opencore_cloud_4',
  'landing_v2.opencore_cloud_5',
];

export const OpenCoreSection: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="bg-white" style={{ padding: '8rem 2rem' }}>
      <div className="max-w-[1200px] mx-auto">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-[#5B6660] mb-4">
          {t('landing_v2.opencore_eyebrow')}
        </p>
        <h2
          className="text-[#0E1A14] font-semibold mb-6 max-w-[20ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2rem, 4vw, 3rem)',
            lineHeight: 1.15,
            letterSpacing: '-0.02em',
          }}
        >
          {t('landing_v2.opencore_h2')}
        </h2>
        <p className="text-[#5B6660] text-base leading-relaxed mb-12 max-w-[64ch]">
          {t('landing_v2.opencore_intro')}
        </p>

        <div className="max-w-[1200px] mx-auto relative flex flex-col md:flex-row">
          <div className="flex-1 md:pr-16">
            <img
              src="/media/nkz-os-logo.svg"
              alt="nkz-os"
              className="h-7 w-auto mb-5"
            />
            <h3
              className="text-[#0E1A14] font-semibold mb-6"
              style={{ fontFamily: "'Inter', sans-serif", fontSize: '22px' }}
            >
              {t('landing_v2.opencore_oss_title')}
            </h3>
            <ul className="space-y-3.5 mb-8">
              {ossItems.map((k) => (
                <li key={k} className="text-[#0E1A14] text-[15px]">
                  {t(k)}
                </li>
              ))}
            </ul>
            <a
              href="https://nkz-os.org"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-[#1F4D38] font-medium text-sm hover:underline group"
            >
              {t('landing_v2.opencore_oss_cta')}
              <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
            </a>
          </div>

          <div className="hidden md:block w-px bg-[rgba(14,26,20,0.08)] self-stretch" />

          <div className="flex-1 md:pl-16 pt-8 md:pt-0 border-t md:border-t-0 border-[rgba(14,26,20,0.08)]">
            <h3
              className="text-[#0E1A14] font-semibold mb-6"
              style={{ fontFamily: "'Inter', sans-serif", fontSize: '22px' }}
            >
              {t('landing_v2.opencore_cloud_title')}
            </h3>
            <ul className="space-y-3.5 mb-8">
              {cloudItems.map((k) => (
                <li key={k} className="text-[#0E1A14] text-[15px]">
                  {t(k)}
                </li>
              ))}
            </ul>
            <button
              onClick={() => navigate('/register')}
              className="inline-flex items-center gap-1.5 text-[#1F4D38] font-medium text-sm hover:underline group"
            >
              {t('landing_v2.opencore_cloud_cta')}
              <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
            </button>
          </div>
        </div>
      </div>
    </section>
  );
};

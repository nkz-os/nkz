import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { ScrollIndicator } from './ScrollIndicator';

export const HeroSection: React.FC = () => {
  const { t } = useI18n();
  const navigate = useNavigate();

  return (
    <section className="relative min-h-screen flex items-end pb-24 overflow-hidden">
      <video
        autoPlay
        muted
        loop
        playsInline
        poster="/media/hero-poster.jpg"
        preload="metadata"
        className="hero-video absolute inset-0 w-full h-full object-cover hidden md:block"
        aria-hidden="true"
      >
        <source src="/media/hero.webm" type="video/webm" />
        <source src="/media/hero.mp4" type="video/mp4" />
      </video>
      <img
        src="/media/hero-poster.jpg"
        alt=""
        className="absolute inset-0 w-full h-full object-cover md:hidden"
        aria-hidden="true"
      />
      <div
        className="absolute inset-0 z-[1]"
        style={{
          background: 'linear-gradient(180deg, rgba(14,26,20,0.30) 0%, rgba(14,26,20,0.55) 50%, rgba(14,26,20,0.85) 100%)',
        }}
      />
      <div className="relative z-[2] max-w-[1200px] mx-auto px-8 w-full">
        <p className="font-mono-landing text-[13px] tracking-[0.15em] uppercase text-white/70 mb-4">
          {t('landing_v2.eyebrow_open_core')}
        </p>
        <h1
          className="text-white font-semibold mb-6 max-w-[14ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(2.5rem, 6vw, 5.5rem)',
            lineHeight: 1.05,
            letterSpacing: '-0.03em',
          }}
        >
          {t('landing_v2.hero_h1')}
        </h1>
        <p
          className="text-white/85 mb-10 max-w-[56ch]"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 'clamp(1.05rem, 1.4vw, 1.25rem)',
            lineHeight: 1.5,
            fontWeight: 400,
          }}
        >
          {t('landing_v2.hero_sub')}
        </p>
        <div className="flex flex-col sm:flex-row items-start gap-4">
          <button
            onClick={() => navigate('/register')}
            className="inline-flex items-center px-7 py-3.5 bg-[#1F4D38] text-white font-semibold rounded-lg hover:bg-[#163A2A] hover:-translate-y-px transition-all duration-200"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.hero_cta_primary')}
          </button>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'info@nekazari.com'}`}
            className="inline-flex items-center gap-1.5 text-white font-medium hover:underline transition-all duration-200 group"
            style={{ fontSize: '1rem' }}
          >
            {t('landing_v2.hero_cta_secondary')}
            <ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" />
          </a>
        </div>
      </div>
      <ScrollIndicator />
    </section>
  );
};

import React from 'react';
import { useI18n } from '@/context/I18nContext';
import { Check } from 'lucide-react';

export const PricingCards: React.FC = () => {
  const { t } = useI18n();

  return (
    <div className="max-w-[1200px] mx-auto px-8 py-24" id="pricing">
      <div className="text-center mb-16">
        <h2 className="text-4xl font-bold text-[#0E1A14] mb-4">
          {t('landing.pricing.title') || 'Planes adaptados a tu terreno'}
        </h2>
        <p className="text-xl text-[#5B6660] max-w-3xl mx-auto">
          {t('landing.pricing.subtitle') || 'Comienza gratis durante 45 días y mejora cuando lo necesites.'}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
        {/* Free Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">Free</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">0€</span>
            <span className="text-xl text-[#5B6660] ml-2">/mes</span>
          </div>
          <p className="text-[#5B6660] mb-6">Para empezar sin compromiso.</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">1 parcela · 1 usuario</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">Módulos esenciales</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">Soporte comunidad</span></li>
          </ul>
          <a
            href="/register"
            className="block w-full py-3 px-6 text-center rounded-lg bg-white text-[#1F4D38] font-semibold border-2 border-[#1F4D38] hover:bg-[#FAFAF7] transition-colors"
          >
            Crear cuenta gratis
          </a>
        </div>

        {/* Pro Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">{t('landing.pricing.pro.title') || 'Pro'}</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">{t('landing.pricing.pro.price') || '49€'}</span>
            <span className="text-xl text-[#5B6660] ml-2">{t('landing.pricing.pro.period') || '/mes'}</span>
          </div>
          <p className="text-[#5B6660] mb-6">{t('landing.pricing.pro.desc') || 'For professional agronomists and mid-size farms.'}</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat1') || '45-day free trial'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat2') || 'Up to 500 hectares and 5 users'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat3') || 'All modules included'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.pro.feat4') || 'Priority support'}</span></li>
          </ul>
          <a
            href="/register"
            className="block w-full py-3 px-6 text-center rounded-lg bg-[#1F4D38] text-white font-semibold hover:bg-[#163A2A] transition-colors"
          >
            {t('landing.pricing.pro.cta') || 'Start Free Trial'}
          </a>
        </div>

        {/* Enterprise Tier */}
        <div className="bg-white rounded-lg border border-[rgba(14,26,20,0.08)] p-8">
          <h3 className="text-2xl font-bold text-[#0E1A14] mb-2">{t('landing.pricing.ent.title') || 'Enterprise'}</h3>
          <div className="flex items-baseline mb-4">
            <span className="text-4xl font-extrabold text-[#0E1A14]">{t('landing.pricing.ent.price') || 'Custom'}</span>
          </div>
          <p className="text-[#5B6660] mb-6">{t('landing.pricing.ent.desc') || 'For cooperatives, large estates and institutions.'}</p>
          <ul className="space-y-3 mb-8">
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat1') || 'Unlimited hectares and users'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat2') || 'All modules included (AI, Lidar, etc.)'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat3') || 'Dedicated tenant isolation'}</span></li>
            <li className="flex items-start"><Check className="h-5 w-5 text-[#1F4D38] mr-3 shrink-0 mt-0.5" /><span className="text-[#0E1A14] text-sm">{t('landing.pricing.ent.feat4') || 'SLA and account manager'}</span></li>
          </ul>
          <a
            href={`mailto:${(window as any).__ENV__?.SALES_EMAIL || 'sales@example.com'}`}
            className="block w-full py-3 px-6 text-center rounded-lg bg-white text-[#0E1A14] border-2 border-[rgba(14,26,20,0.18)] font-semibold hover:border-[#0E1A14] transition-colors"
          >
            {t('landing.pricing.ent.cta') || 'Contact Sales'}
          </a>
        </div>
      </div>
    </div>
  );
};

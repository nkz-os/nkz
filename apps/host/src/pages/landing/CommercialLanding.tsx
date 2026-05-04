import React, { useState, useEffect } from 'react';
import { useI18n } from '@/context/I18nContext';
import { CookieBanner } from '@/components/CookieBanner';
import { PricingCards } from '@/components/pricing/PricingCards';
import { PartnerLogos } from '@/components/partners/PartnerLogos';

import { HeroTopBar } from './blocks/HeroTopBar';
import { HeroSection } from './blocks/HeroSection';
import { ProofBand } from './blocks/ProofBand';
import { ProductAnchor } from './blocks/ProductAnchor';
import { OpenCoreSection } from './blocks/OpenCoreSection';
import { EcosystemModules } from './blocks/EcosystemModules';
import { OpenStandards } from './blocks/OpenStandards';
import { FinalCTA } from './blocks/FinalCTA';
import { LandingFooter } from './blocks/LandingFooter';

export const CommercialLanding: React.FC = () => {
  const { setLanguage, language, supportedLanguages } = useI18n();
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);
  const [isScrolled, setIsScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > window.innerHeight * 0.5);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const handleLanguageChange = async (lang: string) => {
    await setLanguage(lang as any);
    setShowLanguageMenu(false);
  };

  return (
    <div className="min-h-screen bg-white">
      <CookieBanner />

      <HeroTopBar
        isScrolled={isScrolled}
        language={language}
        supportedLanguages={supportedLanguages}
        showLanguageMenu={showLanguageMenu}
        setShowLanguageMenu={setShowLanguageMenu}
        onLanguageChange={handleLanguageChange}
      />

      {/* Block 2: Hero */}
      <HeroSection />

      {/* Block 3: Proof band */}
      <ProofBand />

      {/* Block 4: Product anchor */}
      <ProductAnchor />

      {/* Block 5: Open Core */}
      <OpenCoreSection />

      {/* Block 6: Ecosystem modules */}
      <EcosystemModules />

      {/* Block 7: Open standards */}
      <OpenStandards />

      {/* Block 8: Pricing */}
      <PricingCards />

      {/* Block 9: Partners */}
      <PartnerLogos />

      {/* Block 10: Final CTA */}
      <FinalCTA />

      {/* Block 11: Footer */}
      <LandingFooter
        language={language}
        supportedLanguages={supportedLanguages}
        showLanguageMenu={showLanguageMenu}
        setShowLanguageMenu={setShowLanguageMenu}
        onLanguageChange={handleLanguageChange}
      />
    </div>
  );
};

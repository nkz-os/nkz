/**
 * useScrollHeaderState — sets CSS var `--nkz-page-header-h` to 56px (compact)
 * or 96px (expanded) based on scroll position, with hysteresis for smooth
 * transitions.
 */
import { useEffect, useRef } from 'react';

export interface UseScrollHeaderStateOptions {
  compactAt?: number;
  hysteresis?: number;
}

export function useScrollHeaderState({
  compactAt = 32,
  hysteresis = 8,
}: UseScrollHeaderStateOptions = {}): void {
  const isCompactRef = useRef(false);

  useEffect(() => {
    let ticking = false;

    const handleScroll = () => {
      if (!ticking) {
        window.requestAnimationFrame(() => {
          const scrollY = window.scrollY;

          if (scrollY > compactAt && !isCompactRef.current) {
            isCompactRef.current = true;
            document.documentElement.style.setProperty(
              '--nkz-page-header-h',
              '56px',
            );
          } else if (scrollY < compactAt - hysteresis && isCompactRef.current) {
            isCompactRef.current = false;
            document.documentElement.style.setProperty(
              '--nkz-page-header-h',
              '96px',
            );
          }

          ticking = false;
        });
        ticking = true;
      }
    };

    // Set initial expanded height
    document.documentElement.style.setProperty('--nkz-page-header-h', '96px');

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', handleScroll);
      document.documentElement.style.removeProperty('--nkz-page-header-h');
    };
  }, [compactAt, hysteresis]);
}

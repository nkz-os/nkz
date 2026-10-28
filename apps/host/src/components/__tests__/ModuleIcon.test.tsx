import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  HeartPulse,
  Layers,
  Link as LinkIcon,
  MessageCircle,
  MessagesSquare,
  Satellite,
  ShieldAlert,
  Sprout,
  Tractor,
  Trees,
  Warehouse,
  Droplets,
} from 'lucide-react';
import { ModuleIcon, resolveModuleIcon, isIconUrl } from '../ModuleIcon';

describe('ModuleIcon', () => {
  it('validates icon URLs strictly (http, https, /, data:image/)', () => {
    expect(isIconUrl('https://example.org/module-icons/intelligence.svg')).toBe(true);
    expect(isIconUrl('/modules/demo/icon.svg')).toBe(true);
    expect(isIconUrl('data:image/svg+xml;utf8,<svg></svg>')).toBe(true);
    expect(isIconUrl('tractor')).toBe(false);
    expect(isIconUrl('sprout')).toBe(false);
    expect(isIconUrl('🧠')).toBe(false);
    expect(isIconUrl('')).toBe(false);
  });

  it('renders an <img> for a real icon_url and falls back to Lucide icon on error', () => {
    const { container } = render(
      <ModuleIcon
        module={{
          id: 'intelligence',
          icon_url: 'https://example.org/module-icons/intelligence.svg',
          metadata: { icon: 'brain-circuit' },
        }}
        alt="Intelligence"
        variant="card"
      />,
    );

    const img = screen.getByRole('img', { name: 'Intelligence' });
    expect(img).toBeInTheDocument();

    fireEvent.error(img);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('resolves Lucide icon when icon_url stores a Lucide name (publish bug convention)', () => {
    const { container } = render(
      <ModuleIcon
        module={{
          id: 'field-operations',
          icon_url: 'tractor',
        }}
        alt="Field Operations"
        variant="card"
      />,
    );

    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('resolves Lucide icon for SQL-registered pseudo-modules with icon_url=null and icon=droplets', () => {
    const resolved = resolveModuleIcon({
      id: 'hydrology',
      icon_url: null,
      icon: 'droplets',
      metadata: { icon: 'droplets' },
    });
    expect(resolved.url).toBeNull();
    expect(resolved.IconComponent).toBe(Droplets);

    const { container } = render(
      <ModuleIcon
        module={{ id: 'hydrology', icon_url: null, icon: 'droplets' }}
        variant="card"
      />,
    );
    expect(container.querySelector('svg')).toBeInTheDocument();
    expect(screen.queryByText('droplets')).not.toBeInTheDocument();
  });

  it('disambiguates colliding module IDs and fixes soil terrain', () => {
    expect(resolveModuleIcon({ id: 'soil', icon: 'terrain' }).IconComponent).toBe(Layers);
    expect(resolveModuleIcon({ id: 'bioorchestrator', icon: 'sprout' }).IconComponent).toBe(Sprout);
    expect(resolveModuleIcon({ id: 'crop-health', icon: 'sprout' }).IconComponent).toBe(HeartPulse);
    expect(resolveModuleIcon({ id: 'greenhouse-dt', icon: 'sprout' }).IconComponent).toBe(Warehouse);
    expect(resolveModuleIcon({ id: 'carbon', icon: 'leaf' }).IconComponent).toBe(Trees);
    expect(resolveModuleIcon({ id: 'vegetation-prime', icon: 'leaf' }).IconComponent).toBe(Satellite);
    expect(resolveModuleIcon({ id: 'vegetation-health', icon: 'leaf' }).IconComponent).toBe(Satellite);
    expect(resolveModuleIcon({ id: 'zulip', icon: 'message-circle' }).IconComponent).toBe(MessagesSquare);
    expect(resolveModuleIcon({ id: 'agent', icon: 'message-circle' }).IconComponent).toBe(MessageCircle);
    expect(resolveModuleIcon({ id: 'jd-connect', icon: 'puzzle' }).IconComponent).toBe(LinkIcon);
    expect(resolveModuleIcon({ id: 'field-operations', icon: 'tractor' }).IconComponent).toBe(Tractor);
    expect(resolveModuleIcon({ id: 'risk', icon: 'puzzle' }).IconComponent).toBe(ShieldAlert);
  });

  it('renders emoji fallback when no Lucide icon matches', () => {
    render(
      <ModuleIcon
        module={{
          id: 'custom-emoji-mod',
          icon_url: null,
          metadata: { icon: '🧠' },
        }}
        variant="card"
      />,
    );

    expect(screen.getByText('🧠')).toBeInTheDocument();
  });
});

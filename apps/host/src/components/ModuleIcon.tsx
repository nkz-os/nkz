import React, { useState, useEffect } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart2,
  Bell,
  Bird,
  BookOpen,
  Bot,
  Brain,
  BrainCircuit,
  Briefcase,
  Building2,
  Cloud,
  CloudSun,
  Cpu,
  DatabaseBackup,
  Droplets,
  Gauge,
  HeartPulse,
  Layers,
  Leaf,
  LineChart,
  Link as LinkIcon,
  Map,
  MapPin,
  MessageCircle,
  MessagesSquare,
  Mountain,
  MountainSnow,
  Network,
  Notebook,
  Package,
  Puzzle,
  Radio,
  Recycle,
  Route,
  Satellite,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Sprout,
  Tractor,
  Trees,
  Warehouse,
  Wifi,
  Workflow,
  Zap,
} from 'lucide-react';

export type LucideIconComponent = React.ComponentType<{ className?: string }>;

export interface ModuleIconSource {
  id?: string;
  name?: string;
  displayName?: string;
  display_name?: string;
  icon?: string | null;
  icon_url?: string | null;
  metadata?: {
    icon?: string | null;
    icon_url?: string | null;
    [key: string]: unknown;
  } | null;
}

/**
 * Per-module ID overrides to disambiguate modules that share the same
 * icon name in their manifest (e.g. sprout, leaf, message-circle, puzzle)
 * or declare a non-existent Lucide name (soil -> terrain).
 */
export const MODULE_ID_ICON_MAP: Record<string, LucideIconComponent> = {
  'soil': Layers,
  'bioorchestrator': Sprout,
  'crop-health': HeartPulse,
  'greenhouse-dt': Warehouse,
  'carbon': Trees,
  'vegetation-prime': Satellite,
  'vegetation-health': Satellite,
  'zulip': MessagesSquare,
  'agent': MessageCircle,
  'jd-connect': LinkIcon,
  'field-operations': Tractor,
  'risk': ShieldAlert,
  'risks': ShieldAlert,
};

/**
 * Explicit named-import map so Vite/Rollup tree-shakes lucide-react.
 */
export const LUCIDE_ICON_MAP: Record<string, LucideIconComponent> = {
  'activity': Activity,
  'alert': AlertTriangle,
  'alert-triangle': AlertTriangle,
  'bar-chart-2': BarChart2,
  'bell': Bell,
  'bird': Bird,
  'book-open': BookOpen,
  'bot': Bot,
  'brain': Brain,
  'brain-circuit': BrainCircuit,
  'briefcase': Briefcase,
  'building-2': Building2,
  'chart': BarChart2,
  'cloud': Cloud,
  'cloud-sun': CloudSun,
  'cpu': Cpu,
  'database-backup': DatabaseBackup,
  'droplet': Droplets,
  'droplets': Droplets,
  'gauge': Gauge,
  'heart-pulse': HeartPulse,
  'layers': Layers,
  'leaf': Leaf,
  'line-chart': LineChart,
  'link': LinkIcon,
  'map': Map,
  'map-pin': MapPin,
  'message-circle': MessageCircle,
  'messages-square': MessagesSquare,
  'mountain': Mountain,
  'mountain-snow': MountainSnow,
  'network': Network,
  'notebook': Notebook,
  'package': Package,
  'puzzle': Puzzle,
  'radio': Radio,
  'recycle': Recycle,
  'route': Route,
  'satellite': Satellite,
  'shield-alert': ShieldAlert,
  'shield-check': ShieldCheck,
  'sparkles': Sparkles,
  'sprout': Sprout,
  'terrain': Layers,
  'tractor': Tractor,
  'trees': Trees,
  'warehouse': Warehouse,
  'wifi': Wifi,
  'workflow': Workflow,
  'zap': Zap,
};

export function isIconUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  return (
    trimmed.startsWith('http://') ||
    trimmed.startsWith('https://') ||
    trimmed.startsWith('/') ||
    trimmed.startsWith('data:image/')
  );
}

export function normalizeIconKey(raw: string): string {
  return raw
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/[\s_]+/g, '-')
    .toLowerCase();
}

export function isEmojiIcon(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 4) return false;
  // Must contain at least one non-ASCII character so short ASCII strings aren't treated as emojis
  return Array.from(trimmed).some((ch) => (ch.codePointAt(0) ?? 0) > 127);
}

export interface ResolvedModuleIcon {
  url: string | null;
  IconComponent: LucideIconComponent | null;
  emoji: string | null;
}

export function resolveModuleIcon(module: ModuleIconSource): ResolvedModuleIcon {
  const urlCandidate = [module.icon_url, module.metadata?.icon_url, module.icon].find(isIconUrl) ?? null;

  const moduleId = typeof module.id === 'string' ? module.id.trim().toLowerCase() : '';
  const idOverride = moduleId ? MODULE_ID_ICON_MAP[moduleId] : undefined;

  let namedIcon: LucideIconComponent | undefined = idOverride;
  if (!namedIcon) {
    const nameCandidates = [
      module.icon,
      module.metadata?.icon,
      !isIconUrl(module.icon_url) ? module.icon_url : undefined,
    ];
    for (const candidate of nameCandidates) {
      if (typeof candidate === 'string' && candidate.trim() && !isIconUrl(candidate)) {
        const key = normalizeIconKey(candidate);
        if (LUCIDE_ICON_MAP[key] && key !== 'puzzle') {
          namedIcon = LUCIDE_ICON_MAP[key];
          break;
        }
      }
    }
  }

  let emoji: string | null = null;
  if (!namedIcon) {
    const emojiCandidates = [module.metadata?.icon, module.icon];
    for (const candidate of emojiCandidates) {
      if (isEmojiIcon(candidate)) {
        emoji = candidate.trim();
        break;
      }
    }
  }

  // If no override, no specific Lucide icon, and no emoji, check if candidate was explicitly 'puzzle'
  if (!namedIcon && !emoji) {
    const nameCandidates = [
      module.icon,
      module.metadata?.icon,
      !isIconUrl(module.icon_url) ? module.icon_url : undefined,
    ];
    for (const candidate of nameCandidates) {
      if (typeof candidate === 'string' && normalizeIconKey(candidate) === 'puzzle') {
        namedIcon = Puzzle;
        break;
      }
    }
  }

  return {
    url: urlCandidate ? urlCandidate.trim() : null,
    IconComponent: namedIcon ?? null,
    emoji,
  };
}

export interface ModuleIconProps {
  module: ModuleIconSource;
  alt?: string;
  variant?: 'card' | 'nav' | 'inline';
  inactive?: boolean;
  iconClassName?: string;
  className?: string;
}

export const ModuleIcon: React.FC<ModuleIconProps> = ({
  module,
  alt = '',
  variant = 'nav',
  inactive = false,
  iconClassName,
  className = '',
}) => {
  const { url, IconComponent, emoji } = resolveModuleIcon(module);
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    setImgError(false);
  }, [url]);

  const showImage = Boolean(url && !imgError);

  if (variant === 'card') {
    if (showImage && url) {
      return (
        <img
          src={url}
          alt={alt}
          className={`w-12 h-12 rounded-lg object-cover border border-nkz-border ${inactive ? 'grayscale opacity-50' : ''} ${className}`.trim()}
          onError={() => setImgError(true)}
        />
      );
    }

    const FallbackIcon = IconComponent ?? Package;
    return (
      <div
        className={`w-12 h-12 rounded-lg bg-nkz-bg-secondary flex items-center justify-center ${inactive ? 'opacity-50' : ''} ${className}`.trim()}
      >
        {IconComponent ? (
          <IconComponent
            className={iconClassName ?? `w-6 h-6 ${inactive ? 'text-gray-300' : 'text-nkz-muted'}`}
          />
        ) : emoji ? (
          <span className="text-xl leading-none" aria-hidden="true">
            {emoji}
          </span>
        ) : (
          <FallbackIcon
            className={iconClassName ?? `w-6 h-6 ${inactive ? 'text-gray-300' : 'text-nkz-muted'}`}
          />
        )}
      </div>
    );
  }

  if (variant === 'inline') {
    if (showImage && url) {
      return (
        <img
          src={url}
          alt={alt}
          className={`w-4 h-4 rounded object-contain flex-shrink-0 ${className}`.trim()}
          onError={() => setImgError(true)}
        />
      );
    }
    if (IconComponent) {
      return (
        <IconComponent
          className={iconClassName ?? `w-4 h-4 text-slate-500 flex-shrink-0 ${className}`.trim()}
          aria-hidden="true"
        />
      );
    }
    if (emoji) {
      return (
        <span className={`w-4 text-center flex-shrink-0 ${className}`.trim()} aria-hidden="true">
          {emoji}
        </span>
      );
    }
    return (
      <Puzzle
        className={iconClassName ?? `w-4 h-4 text-slate-500 flex-shrink-0 ${className}`.trim()}
        aria-hidden="true"
      />
    );
  }

  // variant === 'nav'
  if (showImage && url) {
    return (
      <img
        src={url}
        alt={alt}
        className={`w-5 h-5 rounded object-contain flex-shrink-0 ${className}`.trim()}
        onError={() => setImgError(true)}
      />
    );
  }

  if (IconComponent) {
    return (
      <IconComponent
        className={iconClassName ?? `w-5 h-5 flex-shrink-0 text-nkz-muted ${className}`.trim()}
      />
    );
  }

  if (emoji) {
    return (
      <span
        className={`w-5 h-5 flex items-center justify-center text-base flex-shrink-0 ${className}`.trim()}
        aria-hidden="true"
      >
        {emoji}
      </span>
    );
  }

  return (
    <Puzzle
      className={iconClassName ?? `w-5 h-5 flex-shrink-0 text-nkz-muted ${className}`.trim()}
    />
  );
};

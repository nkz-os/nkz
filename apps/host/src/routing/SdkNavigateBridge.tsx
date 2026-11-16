// =============================================================================
// SDK navigate bridge
// =============================================================================
// Contract: `window.__NKZ_SDK__.navigate(path)` moves the host router to an
// in-app path (must start with a single "/"; query string kept). Federated
// modules call it to open another module; without it they fall back to a full
// page load, which restarts the host with an empty module list.

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { logger } from '@/utils/logger';

type SdkGlobal = { navigate?: (path: string) => void; [key: string]: unknown };
type WindowWithSdk = Window & { __NKZ_SDK__?: SdkGlobal };

const isInAppPath = (path: unknown): path is string =>
  typeof path === 'string' && path.startsWith('/') && !path.startsWith('//');

export const SdkNavigateBridge = (): null => {
  const navigate = useNavigate();

  useEffect(() => {
    const w = window as WindowWithSdk;
    const bridge = (path: string) => {
      if (!isInAppPath(path)) {
        logger.warn('[SdkNavigateBridge] ignored non in-app path');
        return;
      }
      navigate(path);
    };
    w.__NKZ_SDK__ = { ...(w.__NKZ_SDK__ ?? {}), navigate: bridge };
    return () => {
      if (w.__NKZ_SDK__?.navigate === bridge) {
        delete w.__NKZ_SDK__.navigate;
      }
    };
  }, [navigate]);

  return null;
};

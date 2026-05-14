import { createContext, useContext } from 'react';
import type {
  UseAuthReturn,
  UseI18nReturn,
  UsePlatformEventsReturn,
  OrionTransport,
  ModuleAPITransport,
  FilesTransport,
} from '../hooks/types';

/** Runtime value injected by the provider — real (host) or mock (nkz dev) */
export interface NKZRuntime {
  /** Id of the module consuming this provider — used for event namespacing */
  moduleId: string;
  auth: UseAuthReturn;
  i18n: UseI18nReturn;
  events: UsePlatformEventsReturn;
  orion: OrionTransport;
  moduleApi: ModuleAPITransport;
  files: FilesTransport;
}

export const NKZContext = createContext<NKZRuntime | null>(null);

/** Internal helper used by every hook to read the runtime + fail loudly if missing */
export function useNKZRuntime(): NKZRuntime {
  const runtime = useContext(NKZContext);
  if (!runtime) {
    throw new Error(
      '[@nekazari/module-kit] No NKZProvider in the React tree. ' +
        'In production this is provided by the host; in `nkz dev` use MockProvider from @nekazari/module-kit/mock.',
    );
  }
  return runtime;
}

// =============================================================================
// Remote Module Loader — Federation-backed module rendering
// =============================================================================
// Resolves a module to a React component using two strategies:
// 1. LOCAL: Bundled module from localAddonRegistry (no network).
// 2. FEDERATION: loadRemote('<alias>/Module') from @module-federation/runtime,
//    where <alias> is the module id sanitized to a JS identifier. The remote's
//    default export is a `defineModule(...)` result; we run toNKZRegistration
//    on it to extract the main component and propagate viewerSlots upstream.

import React, { Suspense, ComponentType, ErrorInfo } from 'react';
import { loadRemote } from '@module-federation/runtime';
import { toNKZRegistration } from '@nekazari/module-kit';
import { ModuleDefinition, useModules } from '@/context/ModuleContext';
import { isLocalAddon, getLocalAddon } from '@/config/localAddons';

interface RemoteModuleLoaderProps {
  module: ModuleDefinition;
  fallback?: React.ReactNode;
  errorFallback?: (error: Error) => React.ReactNode;
}

// =============================================================================
// Error Boundary
// =============================================================================

class RemoteModuleErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback?: (error: Error) => React.ReactNode },
  { hasError: boolean; error: Error | null }
> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('[RemoteModuleLoader] Module render error:', error.message, errorInfo.componentStack);
  }

  render() {
    if (this.state.hasError && this.state.error) {
      if (this.props.fallback) {
        return this.props.fallback(this.state.error);
      }
      return (
        <div className="p-4 bg-red-50 border border-red-200 rounded-md">
          <h3 className="text-red-800 font-semibold">Error loading module</h3>
          <p className="text-red-600 text-sm mt-1">
            {this.state.error.message || 'Failed to load remote module'}
          </p>
          <details className="mt-2">
            <summary className="text-xs text-gray-600 cursor-pointer">Stack trace</summary>
            <pre className="text-xs text-gray-500 mt-1 overflow-auto max-h-40">
              {this.state.error.stack}
            </pre>
          </details>
        </div>
      );
    }

    return this.props.children;
  }
}

// =============================================================================
// Loading Fallback
// =============================================================================

const DefaultLoadingFallback: React.FC = () => (
  <div className="flex items-center justify-center min-h-[400px]">
    <div className="text-center">
      <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-green-600 mx-auto mb-4"></div>
      <p className="text-gray-600">Loading module...</p>
    </div>
  </div>
);

// =============================================================================
// Module Loader Component
// =============================================================================

export const RemoteModuleLoader: React.FC<RemoteModuleLoaderProps> = ({
  module,
  fallback = <DefaultLoadingFallback />,
  errorFallback,
}) => {
  const [Component, setComponent] = React.useState<ComponentType<any> | null>(null);
  const [loadError, setLoadError] = React.useState<Error | null>(null);
  const [isLocal, setIsLocal] = React.useState<boolean>(false);
  const { aliasFor, applyModuleRegistration } = useModules();

  React.useEffect(() => {
    let isMounted = true;

    const load = async () => {
      try {
        // STRATEGY 1: Local bundled module
        const shouldLoadLocal = module.isLocal || isLocalAddon(module.id);
        if (shouldLoadLocal && isLocalAddon(module.id)) {
          const localAddon = getLocalAddon(module.id);
          if (localAddon && isMounted) {
            setIsLocal(true);
            setComponent(() => localAddon.component);
            return;
          }
        }

        // STRATEGY 2: Federated remote
        if (!module.remoteEntry) {
          throw new Error(
            `Module ${module.id} has no remote entry URL and is not in localAddons. ` +
            `Ensure the module is registered with a remote_entry_url pointing to mf-manifest.json.`,
          );
        }

        const alias = aliasFor(module.id);
        const exposed = await loadRemote<{ default?: unknown }>(`${alias}/Module`);
        if (!exposed) {
          throw new Error(`loadRemote returned null for "${alias}/Module".`);
        }

        const moduleDef = (exposed as { default?: unknown }).default ?? exposed;
        if (!moduleDef || typeof moduleDef !== 'object') {
          throw new Error(
            `Module "${module.id}" did not default-export a defineModule result. Got: ${typeof moduleDef}`,
          );
        }

        const registration = toNKZRegistration(moduleDef as Parameters<typeof toNKZRegistration>[0]);

        if (!isMounted) return;

        if (registration.viewerSlots && Object.keys(registration.viewerSlots).length > 0) {
          applyModuleRegistration(module.id, registration);
        }

        const main = registration.main as ComponentType<any> | undefined;
        // React components come in multiple forms:
        //   typeof === 'function' — FunctionComponent, ClassComponent, ForwardRef, Memo
        //   typeof === 'object' && $$typeof — Lazy, Context.Provider, Suspense, Fragment
        const canRender =
          main != null &&
          (typeof main === 'function' ||
            (typeof main === 'object' && '$$typeof' in (main as object)));
        if (canRender) {
          setComponent(() => main);
        } else {
          // Module registered slots but has no main page component. Render a
          // placeholder rather than throwing — slot widgets will still mount
          // wherever the host hosts them.
          setComponent(() => () => (
            <div className="p-8 text-center bg-gray-50 rounded-lg border border-gray-200">
              <h3 className="text-lg font-semibold text-gray-700 mb-2">Module Active: {module.id}</h3>
              <p className="text-gray-500">This module is active and has registered its widgets.</p>
              <p className="text-gray-400 text-sm mt-2">No main view component provided.</p>
            </div>
          ));
        }
      } catch (error) {
        console.error(`[RemoteModuleLoader] Failed to load module ${module.id}:`, error);
        if (isMounted) {
          setLoadError(error instanceof Error ? error : new Error('Unknown error loading module'));
        }
      }
    };

    load();

    return () => {
      isMounted = false;
    };
  }, [module, aliasFor, applyModuleRegistration]);

  if (loadError) {
    if (errorFallback) {
      return <>{errorFallback(loadError)}</>;
    }
    return (
      <div className="p-4 bg-red-50 border border-red-200 rounded-md">
        <h3 className="text-red-800 font-semibold">Error loading module: {module.displayName}</h3>
        <p className="text-red-600 text-sm mt-1">{loadError.message}</p>
        {!isLocal && module.remoteEntry && (
          <p className="text-gray-500 text-xs mt-2">
            Remote Entry: {module.remoteEntry}
          </p>
        )}
        <details className="mt-2">
          <summary className="text-xs text-gray-600 cursor-pointer">Stack trace</summary>
          <pre className="text-xs text-gray-500 mt-1 overflow-auto max-h-40">
            {loadError.stack || 'No stack trace available'}
          </pre>
        </details>
      </div>
    );
  }

  if (!Component) {
    return <>{fallback}</>;
  }

  return (
    <div
      className="remote-module-container"
      data-module-id={module.id}
      style={{
        isolation: 'isolate',
        contain: 'layout style paint',
        position: 'relative',
        display: 'block',
      }}
    >
      <RemoteModuleErrorBoundary fallback={errorFallback}>
        <Suspense fallback={fallback}>
          <Component />
        </Suspense>
      </RemoteModuleErrorBoundary>
    </div>
  );
};

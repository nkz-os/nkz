import { useEffect, useState } from 'react';

export type CapabilityValueProps = {
  parcelId: string;
  entityType: string;
  attribute: string;
  apiBase?: string;
};

type EntityShape = Record<string, unknown> & {
  [k: string]: {
    type: string;
    value: unknown;
    providedBy?: { value: string };
    license?: { value: string };
    observedAt?: string;
  };
};

export function CapabilityValue({ parcelId, entityType, attribute, apiBase = '' }: CapabilityValueProps) {
  const [state, setState] = useState<
    | { kind: 'loading' }
    | { kind: 'error'; message: string }
    | { kind: 'noEntitlement' }
    | { kind: 'noData' }
    | { kind: 'ok'; value: unknown; providedBy?: string; license?: string; observedAt?: string }
  >({ kind: 'loading' });

  useEffect(() => {
    const url = `${apiBase}/ngsi-ld/v1/entities?type=${encodeURIComponent(entityType)}&q=refAgriParcel=="urn:ngsi-ld:AgriParcel:${encodeURIComponent(parcelId)}"`;
    fetch(url, { credentials: 'include' })
      .then(async (r) => {
        if (r.status === 403) { setState({ kind: 'noEntitlement' }); return; }
        if (!r.ok) { setState({ kind: 'error', message: `HTTP ${r.status}` }); return; }
        const list = (await r.json()) as EntityShape[];
        const entity = list[0];
        if (!entity) { setState({ kind: 'noData' }); return; }
        const attr = entity[attribute];
        if (!attr) { setState({ kind: 'noData' }); return; }
        setState({
          kind: 'ok',
          value: attr.value,
          providedBy: attr.providedBy?.value,
          license: attr.license?.value,
          observedAt: attr.observedAt,
        });
      })
      .catch((e) => setState({ kind: 'error', message: String(e) }));
  }, [apiBase, parcelId, entityType, attribute]);

  if (state.kind === 'loading') return <span aria-busy="true">Loading…</span>;
  if (state.kind === 'error') return <span role="alert">Error: {state.message}</span>;
  if (state.kind === 'noEntitlement') return <span className="nkz-no-entitlement">Entitlement required</span>;
  if (state.kind === 'noData') return <span className="nkz-no-data">No data</span>;
  const provenance = [state.providedBy, state.license, state.observedAt].filter(Boolean).join(' · ');
  return (
    <span className="nkz-capability-value">
      <strong>{String(state.value)}</strong>
      {provenance && <small> · {provenance}</small>}
    </span>
  );
}

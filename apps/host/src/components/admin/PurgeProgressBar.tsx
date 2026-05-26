import React from 'react';
import { Check, X, Loader2, Circle } from 'lucide-react';

interface PhaseResult {
  phase: string;
  ok: boolean;
  error?: string;
}

interface PurgeProgressBarProps {
  phases: PhaseResult[];
  running: boolean;
}

const PHASE_LABELS: Record<string, string> = {
  precheck: 'Pre-check',
  cut_access: 'Cutting access',
  subscriptions: 'Cancelling subscriptions',
  ngsi_ld_data: 'Deleting NGSI-LD data',
  relational_data: 'Deleting relational data',
  storage: 'Cleaning storage',
  infrastructure: 'Destroying infrastructure',
  close: 'Closing',
};

export const PurgeProgressBar: React.FC<PurgeProgressBarProps> = ({ phases, running }) => {
  const allPhases = ['precheck', 'cut_access', 'subscriptions', 'ngsi_ld_data', 'relational_data', 'storage', 'infrastructure', 'close'];

  return (
    <div className="space-y-2">
      {allPhases.map((phaseName, i) => {
        const done = phases.find(p => p.phase === phaseName);
        const isCurrent = running && phases.length === i;

        return (
          <div key={phaseName} className="flex items-center gap-3">
            <div className="w-6 h-6 flex items-center justify-center">
              {done?.ok === true && <Check className="h-5 w-5 text-nkz-success" />}
              {done?.ok === false && <X className="h-5 w-5 text-nkz-danger" />}
              {!done && isCurrent && <Loader2 className="h-5 w-5 animate-spin text-nkz-accent-base" />}
              {!done && !isCurrent && <Circle className="h-5 w-5 text-nkz-text-muted" />}
            </div>
            <span className={`text-sm ${isCurrent ? 'font-bold text-nkz-accent-base' : done?.ok === false ? 'text-nkz-danger' : 'text-nkz-text-secondary'}`}>
              {PHASE_LABELS[phaseName] || phaseName}
            </span>
            {done?.error && <span className="text-xs text-nkz-danger ml-auto">{done.error}</span>}
          </div>
        );
      })}
    </div>
  );
};

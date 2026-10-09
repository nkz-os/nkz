import { useEffect, useState } from 'react';
import { api } from '@/services/api';
import type { CropCycles } from '@/utils/campaignRange';

/** The platform's crop-cycle resolution for a parcel (null while loading or on error). */
export function useParcelCropCycles(parcelId: string | null): CropCycles | null {
  const [cycles, setCycles] = useState<CropCycles | null>(null);
  useEffect(() => {
    setCycles(null);
    if (!parcelId || !parcelId.includes(':AgriParcel:')) return;
    let alive = true;
    api.get(`/api/entities/parcels/${encodeURIComponent(parcelId)}/crop-cycles`)
      .then((r) => { if (alive) setCycles(r.data as CropCycles); })
      .catch(() => { if (alive) setCycles(null); });
    return () => { alive = false; };
  }, [parcelId]);
  return cycles;
}

import { Select } from '@nekazari/ui-kit';
import type { ParkOption } from '@/utils/parcelLinks';

/** Option value for "the parcel owns the sensor"; Select does not accept an empty value. */
export const PARCEL_OWNER = 'parcel';

interface SensorOwnerSelectProps {
  parks: ParkOption[];
  ownerParkId: string | null;
  onChange: (ownerParkId: string | null) => void;
  label: string;
  parcelLabel: string;
  parkLabel: string;
}

/** Owner of a fixed sensor: its parcel, or a solar park on the selected parcels. Hidden without parks. */
export function SensorOwnerSelect({
  parks,
  ownerParkId,
  onChange,
  label,
  parcelLabel,
  parkLabel,
}: SensorOwnerSelectProps) {
  if (parks.length === 0) return null;
  return (
    <div>
      <label className="block text-sm font-medium text-nkz-text-primary mb-1">{label}</label>
      <Select
        value={ownerParkId ?? PARCEL_OWNER}
        onValueChange={(value) => onChange(value === PARCEL_OWNER ? null : value)}
        options={[
          { value: PARCEL_OWNER, label: parcelLabel },
          ...parks.map((park) => ({ value: park.id, label: `${parkLabel}: ${park.name}` })),
        ]}
      />
    </div>
  );
}

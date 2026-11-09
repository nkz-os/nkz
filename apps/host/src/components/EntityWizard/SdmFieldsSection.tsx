import { Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { SDM_FIELDS, optionKey } from './sdmFields';

interface Props {
  entityType: string;
  values: Record<string, string | number>;
  onChange: (values: Record<string, string | number>) => void;
}

/** The Smart Data Models fields of a canonical type; required ones are marked. */
export function SdmFieldsSection({ entityType, values, onChange }: Props) {
  const { t } = useI18n();
  const fields = SDM_FIELDS[entityType];
  if (!fields) return null;

  const set = (key: string, value: string) => onChange({ ...values, [key]: value });

  return (
    <div className="pt-4 border-t border-nkz-border space-y-3">
      <h4 className="text-sm font-medium text-nkz-text-primary">{t('wizard.sdm.title')}</h4>
      {fields.map(f => {
        const id = `sdm-${f.key}`;
        const label = `${t(`wizard.sdm.fields.${f.key}`)}${f.required ? ' *' : ''}`;
        const value = String(values[f.key] ?? '');
        return (
          <div key={f.key}>
            <label htmlFor={id} className="block text-xs font-medium text-nkz-text-secondary mb-1">{label}</label>
            {f.kind === 'select' ? (
              <select
                id={id}
                value={value}
                onChange={e => set(f.key, e.target.value)}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              >
                <option value="">{t('wizard.asset.select_option')}</option>
                {(f.options ?? []).map(o => (
                  <option key={o} value={o}>{f.translateOptions ? t(optionKey(f.key, o)) : o}</option>
                ))}
              </select>
            ) : (
              <Input
                id={id}
                type={f.kind === 'date' ? 'date' : 'text'}
                value={value}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => set(f.key, e.target.value)}
                className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-green-500"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

import { useTranslation } from 'react-i18next';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@nekazari/ui-kit';

/** Shown after a sensor was registered on a datalogger whose broker account must be re-issued. */
export function DataloggerRotationNotice({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 bg-black bg-opacity-60 flex items-center justify-center z-[110] p-4">
      <div
        role="alertdialog"
        aria-label={t('dataloggers.needs_rotation.title')}
        className="bg-nkz-surface text-nkz-text-primary rounded-2xl shadow-2xl max-w-md w-full p-6 space-y-4"
      >
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-nkz-warning-strong shrink-0" />
          <h2 className="text-lg font-bold text-nkz-text-primary">{t('dataloggers.needs_rotation.title')}</h2>
        </div>
        <p className="text-sm text-nkz-text-secondary">{t('dataloggers.needs_rotation.message')}</p>
        <div className="flex gap-2 justify-end">
          <a href="/settings" className="text-sm font-medium text-nkz-info underline self-center">
            {t('dataloggers.needs_rotation.open_settings')}
          </a>
          <Button type="button" variant="primary" onClick={onClose}>
            {t('dataloggers.needs_rotation.understood')}
          </Button>
        </div>
      </div>
    </div>
  );
}

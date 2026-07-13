import React, { useEffect, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, Layers } from 'lucide-react'
import CoreLayerToggles from '@/components/viewer/CoreLayerToggles'
import { SlotRenderer } from '@/components/SlotRenderer'
import { useRiskOverlay } from '@/hooks/cesium/useRiskOverlay'
import { useI18n } from '@/context/I18nContext'
import type { SubmenuSide } from './positionFlip'
import { Button } from '@nekazari/ui-kit';

const surfaceBase =
  'bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 shadow-lg'

interface CollapsibleGroupProps {
  storageKey: string
  defaultOpen: boolean
  title: string
  children: React.ReactNode
}

const CollapsibleGroup: React.FC<CollapsibleGroupProps> = ({
  storageKey,
  defaultOpen,
  title,
  children,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem(storageKey)
      return v === null ? defaultOpen : v === 'true'
    } catch {
      return defaultOpen
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, String(isOpen))
    } catch {
      /* quota or private mode */
    }
  }, [storageKey, isOpen])
  return (
    <div className="py-1">
      <Button
        type="button"
        onClick={() => setIsOpen(v => !v)}
        aria-expanded={isOpen}
        aria-label={title}
        className="w-full px-3 py-1 flex items-center justify-between text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300"
      >
        <span className="text-xs font-semibold uppercase tracking-wider">{title}</span>
        {isOpen ? (
          <ChevronDown className="w-3.5 h-3.5" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5" />
        )}
      </Button>
      {isOpen && <div className="px-2 pt-1 pb-2 space-y-1">{children}</div>}
    </div>
  )
}

export interface LayersCascadeSubmenuProps {
  isOpen: boolean
  side: SubmenuSide
  onMouseEnter: () => void
  onMouseLeave: () => void
}

export const LayersCascadeSubmenu: React.FC<LayersCascadeSubmenuProps> = ({
  isOpen,
  side,
  onMouseEnter,
  onMouseLeave,
}) => {
  const { t } = useI18n()
  const { enabled: riskEnabled, setEnabled: setRiskEnabled } = useRiskOverlay()

  const positionClass =
    side === 'right' ? 'left-full top-0 ml-1' : 'right-full top-0 mr-1'

  return (
    <div
      role="region"
      aria-label={t('viewer.layersTitle')}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className={`absolute ${positionClass} min-w-[280px] max-w-[340px] max-h-[calc(100vh-6rem)] overflow-y-auto rounded-xl ${surfaceBase} transition-opacity duration-200 ${
        isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
      }`}
    >
      {!isOpen ? null : (
        <>
          <div className="px-3 py-2 border-b border-slate-200 dark:border-slate-700 flex items-center gap-2 text-slate-500 dark:text-slate-400">
            <Layers className="w-4 h-4" />
            <span className="text-sm font-medium">{t('viewer.layersTitle')}</span>
          </div>

          <CollapsibleGroup
            storageKey="nkz.viewer.layersSubmenu.core.open"
            defaultOpen={true}
            title={t('viewer.layersGroupCore')}
          >
            <CoreLayerToggles />
            <Button
              type="button"
              onClick={() => setRiskEnabled(!riskEnabled)}
              className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                riskEnabled
                  ? 'bg-nkz-error-light text-nkz-error border border-red-200'
                  : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 border border-transparent'
              }`}
            >
              <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1 text-left">{t('viewer.layersRiskOverlay')}</span>
              <span
                className={`text-xs px-1.5 py-0.5 rounded ${
                  riskEnabled ? 'bg-nkz-error-light text-nkz-error' : 'bg-slate-200 text-slate-500'
                }`}
              >
                {riskEnabled ? t('viewer.layers.risk_on') : t('viewer.layers.risk_off')}
              </span>
            </Button>
          </CollapsibleGroup>

          <CollapsibleGroup
            storageKey="nkz.viewer.layersSubmenu.modules.open"
            defaultOpen={true}
            title={t('viewer.layersGroupModules')}
          >
            <SlotRenderer slot="layer-toggle" />
          </CollapsibleGroup>
        </>
      )}
    </div>
  )
}

export default LayersCascadeSubmenu

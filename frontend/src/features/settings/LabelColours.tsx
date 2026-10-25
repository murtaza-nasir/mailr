// Settings → Mail → Labels → Label colours: the scheme the picker opens on (choosing one also recolours
// every scheme-coloured label to the same hue and shade in it), and the offer to convert colours
// from before schemes ("#bg,#fg") to the nearest scheme colour, after a preview.

import { useQueryClient } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import { qk, useLabels } from '../../api/hooks'
import type { Label } from '../../api/types'
import { LabelChip } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { conversionPlan, describeColor, parseToken, resolveColor, SCHEMES, schemeById, tokenOf, type Mode } from '../../lib/labels'
import { toast, toastError } from '../../stores/ui'
import { useLabelScheme } from '../mail/LabelColourPicker'
import { SetRow } from './common'
import { t } from '../../i18n'

interface ColoursOut {
  changed: number[]
  label_scheme: string | null
}

function useRecolour() {
  const qc = useQueryClient()
  return async (body: { scheme?: string; colors?: Record<string, string | null> }) => {
    const r = await api.post<ColoursOut>('/labels/colours', body)
    await Promise.all([qc.invalidateQueries({ queryKey: qk.labels }), qc.invalidateQueries({ queryKey: qk.me })])
    return r
  }
}

// The sample labels on each scheme card, with the hues they take.
const SAMPLES: [string, string][] = [[t('common.work'), 'blue'], [t('settings.labelColours.travel'), 'orange'], [t('settings.labelColours.finance'), 'green'], [t('settings.labelColours.family'), 'purple']]

/** One scheme as labels look in one theme: the samples in a soft shade (2) and the solid shade (4),
 * then all ten hues at shade 3. Colours are given for that theme, whichever theme is on. */
function SchemePanel({ scheme, mode }: { scheme: string; mode: Mode }) {
  const s = schemeById(scheme)
  const chip = (hue: string, shade: number) => {
    const sw = resolveColor(tokenOf(scheme, hue, shade))![mode]
    return { background: sw.bg, color: sw.fg }
  }
  return (
    <span className={`lc-panel ${mode}`} aria-hidden="true">
      <span className="lc-mode">{mode === 'light' ? t('common.light') : t('common.dark')}</span>
      {[2, 4].map((shade) => (
        <span className="lc-chips" key={shade}>
          {SAMPLES.map(([name, hue]) => (
            <span key={name} className="label-chip" style={chip(hue, shade)}>
              {name}
            </span>
          ))}
        </span>
      ))}
      <span className="lc-strip">
        {s.hues.map((h) => (
          <span key={h.id} style={{ background: resolveColor(tokenOf(scheme, h.id, 3))![mode].bg }} title={h.name} />
        ))}
      </span>
    </span>
  )
}

export function LabelColoursSetting() {
  const scheme = useLabelScheme()
  const labels = useLabels().data ?? []
  const recolour = useRecolour()
  const [busy, setBusy] = useState(false)
  const [converting, setConverting] = useState(false)
  const user = labels.filter((l) => !l.role)
  const legacy = user.filter((l) => l.color && !parseToken(l.color))

  const pick = async (next: string) => {
    if (next === scheme || busy) return
    const before = Object.fromEntries(user.filter((l) => parseToken(l.color)).map((l) => [String(l.id), l.color]))
    setBusy(true)
    try {
      const r = await recolour({ scheme: next })
      const name = SCHEMES.find((s) => s.id === next)!.name
      toast({
        message: r.changed.length ? t('settings.labelColours.coloursLabelsRecoloured', { count: r.changed.length, itemName: name }) : t('settings.labelColours.labelColourScheme', { itemName: name }),
        actionLabel: t('common.undo'),
        onAction: () => void recolour({ scheme, colors: before }),
      })
    } catch (e) {
      toastError(e, t('settings.labelColours.couldNotChangeTheLabel'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SetRow
      k={t('settings.labelColours.labelColours')}
      sub={t('settings.labelColours.theColoursTheLabelColour')}
    >
      <div className="lc-schemes" role="radiogroup" aria-label={t('settings.labelColours.labelColourScheme2')}>
        {SCHEMES.map((s) => (
          <label key={s.id} className={`lc-scheme${s.id === scheme ? ' on' : ''}`}>
            <input type="radio" name="label-scheme" aria-label={s.name} aria-describedby={`lc-desc-${s.id}`} checked={s.id === scheme} disabled={busy} onChange={() => void pick(s.id)} />
            <span className="lc-name">{s.name}</span>
            <span className="lc-desc" id={`lc-desc-${s.id}`}>{s.description}</span>
            <span className="lc-previews">
              <SchemePanel scheme={s.id} mode="light" />
              <SchemePanel scheme={s.id} mode="dark" />
            </span>
          </label>
        ))}
      </div>
      {legacy.length > 0 && (
        <div className="lc-offer">
          <span>
            {t('settings.labelColours.fromBeforeColourSchemesThose', { value: t('settings.labelColours.labelsUseColours', { count: legacy.length }) })}
          </span>
          <button type="button" className="btn tonal sm" onClick={() => setConverting(true)}>
            {t('settings.labelColours.convertMyLabels')}
          </button>
        </div>
      )}
      <ConvertDialog open={converting} onClose={() => setConverting(false)} labels={legacy} scheme={scheme} onApply={recolour} />
    </SetRow>
  )
}

function ConvertDialog({ open, onClose, labels, scheme, onApply }: { open: boolean; onClose: () => void; labels: Label[]; scheme: string; onApply: ReturnType<typeof useRecolour> }) {
  const plan = useMemo(() => conversionPlan(labels, scheme), [labels, scheme])
  const [skip, setSkip] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const chosen = plan.filter((p) => !skip.has(p.label.id))
  const apply = async () => {
    setBusy(true)
    const before = Object.fromEntries(chosen.map((p) => [String(p.label.id), p.label.color]))
    try {
      await onApply({ colors: Object.fromEntries(chosen.map((p) => [String(p.label.id), p.to])) })
      onClose()
      toast({ message: t('settings.labelColours.labelsConverted', { count: chosen.length }), actionLabel: t('common.undo'), onAction: () => void onApply({ colors: before }) })
    } catch (e) {
      toastError(e, t('settings.labelColours.couldNotConvertTheLabels'))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('settings.labelColours.convertLabelColours')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!chosen.length || busy} onClick={() => void apply()}>
            {t('settings.labelColours.convertLabels', { count: chosen.length })}
          </button>
        </>
      }
    >
      <p className="muted" style={{ marginTop: 0 }}>
        {t('settings.labelColours.eachLabelGetsTheNearest', { itemName: SCHEMES.find((s) => s.id === scheme)!.name })}
      </p>
      <div className="lc-plan" role="list" aria-label={t('settings.labelColours.conversionPreview')}>
        {plan.map((p) => (
          <label key={p.label.id} className="lc-plan-row" role="listitem">
            <input
              type="checkbox"
              className="checkbox"
              checked={!skip.has(p.label.id)}
              aria-label={t('settings.labelColours.convert', { labelName: p.label.name })}
              onChange={(e) => {
                const n = new Set(skip)
                if (e.target.checked) n.delete(p.label.id)
                else n.add(p.label.id)
                setSkip(n)
              }}
            />
            <LabelChip label={p.label} full />
            <ArrowRight className="lc-arrow" aria-hidden="true" />
            <LabelChip label={{ ...p.label, color: p.to }} full />
            <span className="muted lc-to">{describeColor(p.to)}</span>
          </label>
        ))}
      </div>
    </Dialog>
  )
}

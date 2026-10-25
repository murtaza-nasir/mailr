// Settings → Ambrose → Sweep (backend agent/review.py): your standing rules for every bulk review
// (the setting sweep_rules; /sweep, review_mail and routines), the built-in meaning of each move that
// the rules adjust, and the moves you chose on review cards, remembered per sender (GET/DELETE
// /agent/review-choices) with Remove and Clear all.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../../../api/client'
import { useSettings } from '../../../api/hooks'
import { Skeleton } from '../../../components/bits'
import { ConfirmDialog } from '../../../components/Dialog'
import { formatRelative } from '../../../lib/format'
import { toast, toastError } from '../../../stores/ui'
import { useAssistantName } from '../../rail/assistant/useAssistantName'
import { Section, SetRow } from '../common'
import { useSaveSettings, useTextDraft } from '../save'
import { t } from '../../../i18n'

/** The longest rules the server takes (agent/review.py RULES_MAX). */
export const SWEEP_RULES_MAX = 2000

/** A move remembered for one sender. */
export interface ReviewChoice {
  id: number
  sender: string
  /** trash, archive, read, keep or label:<id>. */
  move: string
  label?: string
  count: number
  updated_at: string | null
}

export const CHOICES_KEY = ['agent', 'review-choices'] as const
const BASE = '/agent/review-choices'

/** The rules as saved: no space around them, at most SWEEP_RULES_MAX characters. */
export function rulesPatch(text: string): { sweep_rules: string } {
  return { sweep_rules: text.trim().slice(0, SWEEP_RULES_MAX) }
}

/** A remembered move in words. */
export function choiceMoveText(c: Pick<ReviewChoice, 'move' | 'label'>): string {
  if (c.move.startsWith('label:')) return t('rail.assistant.review.moveLabel', { label: c.label ?? '' })
  switch (c.move) {
    case 'trash': return t('rail.assistant.review.moveTrash')
    case 'archive': return t('rail.assistant.review.moveArchive')
    case 'read': return t('rail.assistant.review.moveRead')
    default: return t('rail.assistant.review.keep')
  }
}

export function loadChoices(signal?: AbortSignal): Promise<{ items: ReviewChoice[] }> {
  return api.get<{ items: ReviewChoice[] }>(BASE, undefined, signal)
}

/** ACT agent.review_choice_remove @web.settings */
export function removeChoice(id: number): Promise<unknown> {
  return api.del(`${BASE}/${id}`)
}

/** ACT agent.review_choice_clear @web.settings */
export function clearChoices(): Promise<unknown> {
  return api.del(BASE)
}

/** The built-in meaning of each move (agent/review.py prompt), which your rules adjust. */
const DEFINITIONS = [
  ['rail.assistant.review.moveTrash', 'settings.assistant.sweepSection.defTrash'],
  ['rail.assistant.review.moveArchive', 'settings.assistant.sweepSection.defArchive'],
  ['rail.assistant.review.moveRead', 'settings.assistant.sweepSection.defRead'],
  ['rail.assistant.review.keep', 'settings.assistant.sweepSection.defKeep'],
  ['rail.assistant.review.notSure', 'settings.assistant.sweepSection.defUnsure'],
] as const

function Choices() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: CHOICES_KEY, queryFn: ({ signal }) => loadChoices(signal) })
  const [clearing, setClearing] = useState(false)
  const refresh = () => void qc.invalidateQueries({ queryKey: CHOICES_KEY })
  const remove = useMutation({
    mutationFn: (c: ReviewChoice) => removeChoice(c.id),
    onSuccess: (_r, c) => { toast({ message: t('settings.assistant.sweepSection.removed', { sender: c.sender }) }); refresh() },
    onError: (e) => toastError(e, t('common.notChanged')),
  })
  const clear = useMutation({
    mutationFn: clearChoices,
    onSuccess: () => { setClearing(false); toast({ message: t('settings.assistant.sweepSection.cleared') }); refresh() },
    onError: (e) => toastError(e, t('common.notChanged')),
  })
  if (!q.data) return <Skeleton height={64} />
  const items = q.data.items
  return (
    <div className="sw-choices" data-testid="review-choices">
      {items.length === 0 ? <p className="muted">{t('settings.assistant.sweepSection.noChoices')}</p> : (
        <>
          <ul className="tr-rows">
            {items.map((c) => (
              <li key={c.id} className="tr-row" data-testid="review-choice">
                <div className="tr-main">
                  <span className="tr-email">{c.sender}</span>
                  <span className="pill info">{choiceMoveText(c)}</span>
                  {c.updated_at && <span className="muted">{formatRelative(c.updated_at)}</span>}
                  <button type="button" className="btn text sm tr-remove" disabled={remove.isPending}
                    aria-label={t('settings.assistant.sweepSection.removeFor', { sender: c.sender })}
                    onClick={() => remove.mutate(c)}>{t('common.remove')}</button>
                </div>
              </li>
            ))}
          </ul>
          <button type="button" className="btn text sm" onClick={() => setClearing(true)}>{t('settings.assistant.sweepSection.clearAll')}</button>
        </>
      )}
      <ConfirmDialog open={clearing} title={t('settings.assistant.sweepSection.clearTitle')}
        body={t('settings.assistant.sweepSection.clearBody')} confirmLabel={t('settings.assistant.sweepSection.clearAll')} danger
        busy={clear.isPending} onConfirm={() => clear.mutate()} onClose={() => setClearing(false)} />
    </div>
  )
}

/** ACT settings.sweep_rules @web.settings */
export function SweepSection() {
  const name = useAssistantName()
  const s = useSettings()
  const save = useSaveSettings()
  const saved = s.sweep_rules ?? ''
  // The rules save when you leave the field or stop typing.
  const text = useTextDraft(saved, (v) => {
    const patch = rulesPatch(v)
    if (patch.sweep_rules !== saved) void save(patch)
  })
  return (
    <Section title={t('settings.assistant.sweepSection.title')} desc={t('settings.assistant.sweepSection.desc', { name })}>
      <SetRow k={t('settings.assistant.sweepSection.rules')} sub={t('settings.assistant.sweepSection.rulesHint', { name })}>
        <textarea className="textarea as-textarea" rows={4} maxLength={SWEEP_RULES_MAX} value={text.value}
          placeholder={t('settings.assistant.sweepSection.placeholder')} aria-label={t('settings.assistant.sweepSection.rules')}
          data-testid="sweep-rules" onChange={(e) => text.set(e.target.value)} onBlur={text.flush} />
        <p className="as-hint">{t('settings.assistant.sweepSection.length', { count: text.value.length, max: SWEEP_RULES_MAX })}</p>
      </SetRow>
      <SetRow k={t('settings.assistant.sweepSection.definitions')} sub={t('settings.assistant.sweepSection.definitionsHint')}>
        <dl className="sw-defs" data-testid="sweep-definitions">
          {DEFINITIONS.map(([move, def]) => (
            <div key={move}><dt>{t(move)}</dt><dd className="muted">{t(def)}</dd></div>
          ))}
        </dl>
        <p className="as-hint">{t('settings.assistant.sweepSection.alwaysKept')}</p>
      </SetRow>
      <SetRow k={t('settings.assistant.sweepSection.pastChoices')} sub={t('settings.assistant.sweepSection.pastChoicesHint', { name })}>
        <Choices />
      </SetRow>
    </Section>
  )
}

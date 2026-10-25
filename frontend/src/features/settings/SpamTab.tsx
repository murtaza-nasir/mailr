import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { toast, toastError } from '../../stores/ui'
import type { Settings } from '../../api/types'
import { Radio, Section, SetRow } from './common'
import { useInstantSettings, useTextDraft } from './save'
import { LoadFailed, failedEmpty } from '../../components/bits'
import { t } from '../../i18n'
import { useAssistantName } from '../rail/assistant/useAssistantName'

/** GET /spam/domains: senders whose mail goes to Spam because you reported their mail (intel/spam.py). */
interface SpamDomains {
  items: { key: string; added: string | null; marks: number }[]
  marks_needed: number
  window_days: number
}

type Mode = 'off' | 'score' | 'junk'
type Level = 'gentle' | 'normal' | 'strict'

/** The three plain choices and the numbers each sets. Normal is the server's default
 *  (backend api/auth_routes.py DEFAULT_SETTINGS, intel/spam.py). */
export const SPAM_LEVELS: { id: Level; label: string; desc: string; patch: { spam_classifier: Mode; spam_junk_score: number; spam_upstream_unknown_threshold: number } }[] = [
  { id: 'gentle', label: t('settings.spamTab.gentle'), desc: t('settings.spamTab.onlyClearSpamFromNew'),
    patch: { spam_classifier: 'junk', spam_junk_score: 25, spam_upstream_unknown_threshold: 0 } },
  { id: 'normal', label: t('settings.spamTab.normal'), desc: t('settings.spamTab.likelySpamFromNewSenders'),
    patch: { spam_classifier: 'junk', spam_junk_score: 15, spam_upstream_unknown_threshold: 4 } },
  { id: 'strict', label: t('settings.spamTab.strict'), desc: t('settings.spamTab.doubtfulMailFromNewSenders'),
    patch: { spam_classifier: 'junk', spam_junk_score: 10, spam_upstream_unknown_threshold: 3 } },
]

/** Which level the numbers match, or null for numbers set by hand. */
export function spamLevel(s: { spam_classifier?: Mode; spam_junk_score?: number; spam_upstream_unknown_threshold?: number }): Level | null {
  const mode = s.spam_classifier ?? 'junk'
  const junk = s.spam_junk_score ?? 15
  const up = s.spam_upstream_unknown_threshold ?? 4
  return SPAM_LEVELS.find((l) => l.patch.spam_classifier === mode && l.patch.spam_junk_score === junk && l.patch.spam_upstream_unknown_threshold === up)?.id ?? null
}

/** Settings → Mail → Spam & Screener: how strict mailr's own checks are, on top of the
 *  provider's filter. Gentle, Normal or Strict; the numbers under "Fine-tune". Every change
 *  saves at once with Undo. */
export function SpamTab() {
  const assistantName = useAssistantName()
  const { s, set, save } = useInstantSettings()
  const mode: Mode = s.spam_classifier ?? 'junk'
  const junkScore = s.spam_junk_score ?? 15
  const upstream = s.spam_upstream_unknown_threshold ?? 4
  const suspect = s.spam_suspect_score ?? 5
  const level = spamLevel(s)
  const num = (v: string, fallback: number) => {
    const n = Number(v)
    return Number.isFinite(n) ? n : fallback
  }
  // A number saves when you leave its field or stop typing.
  const nums = useTextDraft({ upstream, junkScore, suspect }, (v) => {
    const patch: Partial<Settings> = {}
    if (v.upstream !== upstream) patch.spam_upstream_unknown_threshold = v.upstream
    if (v.junkScore !== junkScore) patch.spam_junk_score = v.junkScore
    if (v.suspect !== suspect) patch.spam_suspect_score = v.suspect
    if (Object.keys(patch).length) void save(patch)
  })
  const n = nums.value
  return (
    <Section title={t('common.spam')}>
      <p className="settings-intro">
        {t('settings.spamTab.yourMailProviderFiltersSpam')}
      </p>

      <SetRow k={t('settings.spamTab.howStrict')} sub={t('settings.spamTab.mailFromNewSendersOnly')}>
        <div role="radiogroup" aria-label={t('settings.spamTab.howStrict2')}>
          {SPAM_LEVELS.map((l) => (
            <Radio<Level | 'custom'> key={l.id} name="spam-level" value={l.id} current={level ?? 'custom'} onChange={() => void save(l.patch)} label={l.label} desc={l.desc} />
          ))}
          {level === null && (
            <Radio<Level | 'custom'> name="spam-level" value="custom" current="custom" onChange={() => undefined} label={t('settings.spamTab.yourOwnNumbers')}
              desc={mode === 'off' ? t('settings.spamTab.mailrsOwnClassifierIsOff') : t('settings.spamTab.setUnderFineTuneBelow')} />
          )}
        </div>
      </SetRow>

      <SpamDomainsRow />

      <SetRow k={t('settings.spamTab.alwaysOn')}>
        <ul className="hint-list">
          <li>{t('settings.spamTab.sendersYouBlockAndFilters')}</li>
          <li>{t('settings.spamTab.mailWhoseNameClaimsA')}</li>
          <li>{t('settings.spamTab.repliesToAConversationThat')}</li>
        </ul>
      </SetRow>

      <details className="set-details" open={level === null || undefined}>
        <summary>{t('settings.spamTab.fineTuneTheNumbers')}</summary>
        <SetRow k={t('settings.spamTab.providerScore')} sub={t('settings.spamTab.newSendersOnly')}>
          <label className="field-inline">
            {t('settings.spamTab.moveToSpamAtA')}
            <input
              className="input num"
              type="number"
              min={0}
              max={20}
              step={0.5}
              value={n.upstream}
              onChange={(e) => nums.set({ ...n, upstream: num(e.target.value, 4) })}
              onBlur={nums.flush}
              aria-label={t('settings.spamTab.providerSpamScoreThresholdFor')}
            />
            {t('settings.spamTab.orMore')}
          </label>
          <p className="hint">
            {t('settings.spamTab.manyProvidersOnlyMoveMail')}
          </p>
        </SetRow>

        <SetRow k={t('settings.spamTab.spamClassifier')} sub={t('settings.spamTab.learnsFromYourSpamNot')}>
          <Radio<Mode> name="sc" value="junk" current={mode} onChange={(v) => set('spam_classifier', v)} label={t('settings.spamTab.scoreAndMove')} desc={t('settings.spamTab.scoreNewMailAndMove')} />
          <Radio<Mode> name="sc" value="score" current={mode} onChange={(v) => set('spam_classifier', v)} label={t('settings.spamTab.scoreOnly')} desc={t('settings.spamTab.recordTheScoreShownUnder')} />
          <Radio<Mode> name="sc" value="off" current={mode} onChange={(v) => set('spam_classifier', v)} label={t('common.off')} />
          {mode === 'junk' && (
            <label className="field-inline">
              {t('settings.spamTab.moveAtAClassifierScore')}
              <input
                className="input num"
                type="number"
                min={5}
                max={50}
                step={1}
                value={n.junkScore}
                onChange={(e) => nums.set({ ...n, junkScore: num(e.target.value, 15) })}
                onBlur={nums.flush}
                aria-label={t('settings.spamTab.classifierScoreThreshold')}
              />
              {t('settings.spamTab.orMore')}
            </label>
          )}
        </SetRow>

        <SetRow k={t('settings.spamTab.likelySpam')} sub={t('settings.spamTab.newSendersOnly')}>
          <label className="field-inline">
            {t('settings.spamTab.noModelReadsMailWith')}
            <input
              className="input num"
              type="number"
              min={0}
              max={50}
              step={0.5}
              value={n.suspect}
              onChange={(e) => nums.set({ ...n, suspect: num(e.target.value, 5) })}
              onBlur={nums.flush}
              aria-label={t('settings.spamTab.spamScoreFromWhichNo')}
            />
            {t('settings.spamTab.orMore')}
          </label>
          <p className="hint">
            {t('settings.spamTab.suchMailGetsRulesOnly', { name: assistantName })}
          </p>
        </SetRow>
      </details>
    </Section>
  )
}

/** Domains (or addresses at shared providers such as gmail.com) learned from your Report spam clicks. */
function SpamDomainsRow() {
  const qc = useQueryClient()
  const q = useQuery<SpamDomains>({ queryKey: ['spam-domains'], queryFn: () => api.get<SpamDomains>('/spam/domains') })
  const remove = (key: string) =>
    api
      .del(`/spam/domains/${encodeURIComponent(key)}`)
      .then(() => {
        toast({ message: t('settings.spamTab.mailFromIsNoLonger', { key }) })
        void qc.invalidateQueries({ queryKey: ['spam-domains'] })
      })
      .catch((e) => toastError(e, t('settings.spamTab.couldNotRemoveIt')))
  const items = q.data?.items ?? []
  return (
    <SetRow k={t('settings.spamTab.learnedFromYou')} sub={t('settings.spamTab.sendersYouReportedAsSpam')}>
      <p className="hint">
        {t('settings.spamTab.whenYouReportMailFrom', { marksNeeded: q.data?.marks_needed ?? 2, count: q.data?.window_days ?? 30 })}
      </p>
      {failedEmpty(q) ? (
        <LoadFailed what={t('settings.spamTab.theLearnedSenders')} q={q} />
      ) : items.length === 0 ? (
        <p className="muted" data-testid="spam-domains-empty">{t('settings.spamTab.nothingLearnedYet')}</p>
      ) : (
        <ul className="hint-list" data-testid="spam-domains">
          {items.map((d) => (
            <li key={d.key}>
              <code>{d.key}</code>
              {' '}
              <button type="button" className="btn text sm" onClick={() => void remove(d.key)} aria-label={t('settings.spamTab.remove', { key: d.key })}>
                {t('common.remove')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </SetRow>
  )
}

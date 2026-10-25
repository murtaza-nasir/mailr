// Step 4: how the inbox is arranged. Simple, Important first or Categories (layout presets), each
// previewed with her own newest mail once some has arrived, else with a sample.

import { Inbox, Star } from 'lucide-react'
import { useApi, useSettings, useUpdateSettings } from '../../../api/hooks'
import type { ConversationSummary } from '../../../api/types'
import { ChoiceCard, StepFrame } from '../guide'
import { stylePreview, toRow, type PreviewRow } from '../preview'
import { INBOX_STYLES, inboxStyleOf, type InboxStyle } from '../steps'
import { t as tr } from '../../../i18n'

function MiniInbox({ style, own }: { style: InboxStyle; own: PreviewRow[] }) {
  const p = stylePreview(style, own)
  return (
    <span className="ob-mini" aria-hidden>
      {p.tabs.length > 0 && (
        <span className="ob-mini-tabs">
          {p.tabs.map((t, i) => (
            <span key={t} className={i === 0 ? 'on' : ''}>
              {t}
            </span>
          ))}
        </span>
      )}
      {p.groups.map((g, gi) => (
        <span key={gi} className="ob-mini-group">
          {g.title && <span className="ob-mini-title">{g.title}</span>}
          {g.rows.length === 0 && <span className="ob-mini-empty">{tr('onboarding.steps.inboxStyle.nothingYet')}</span>}
          {g.rows.map((r, i) => (
            <span key={i} className={`ob-mini-row${r.unread ? ' unread' : ''}`}>
              {r.important ? <Star className="ob-mini-imp" /> : <span className="ob-mini-dot" />}
              <span className="ob-mini-from">{r.from}</span>
              <span className="ob-mini-subj">{r.subject}</span>
            </span>
          ))}
        </span>
      ))}
    </span>
  )
}

export function InboxStyleStep() {
  const s = useSettings()
  const upd = useUpdateSettings()
  const list = useApi<{ items: ConversationSummary[] }>(['onboarding', 'preview'], '/conversations', { view: 'inbox', limit: 8 }, { refetchInterval: 15_000 })
  const own = (list.data?.items ?? []).map(toRow)
  const current = inboxStyleOf(s.inbox_type)
  return (
    <StepFrame
      icon={<Inbox />}
      title={tr('onboarding.steps.inboxStyle.chooseYourInboxStyle')}
      lead={own.length ? tr('onboarding.steps.inboxStyle.hereIsHowYourOwn') : tr('onboarding.steps.inboxStyle.hereIsHowEachStyle')}
      wide
    >
      <div className="ob-styles" role="radiogroup" aria-label={tr('onboarding.steps.inboxStyle.inboxStyle')}>
        {INBOX_STYLES.map((st) => (
          <ChoiceCard key={st.id} on={current === st.id} onSelect={() => upd.mutate({ inbox_type: st.id })} title={st.title} desc={st.desc} testId={`ob-style-${st.id}`} name={st.title}>
            <MiniInbox style={st.id} own={own} />
          </ChoiceCard>
        ))}
      </div>
      {own.length > 0 && <p className="ob-hint ob-center" data-testid="ob-own-preview">{tr('onboarding.steps.inboxStyle.previewsShowYourOwnNewest')}</p>}
    </StepFrame>
  )
}

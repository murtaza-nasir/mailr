import { withAssistantName } from '../../rail/assistant/useAssistantName'
// The last screen: done, a card that introduces notes and lists (NotesIntro), a short tour of five
// tips (skippable), then the inbox.

import { Archive, Clock, HelpCircle, PartyPopper, Search, Tag } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useMe } from '../../../api/hooks'
import { StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { TIPS } from '../steps'
import { NotesIntro } from './NotesIntro'
import { firstName } from './Welcome'
import { t as tr } from '../../../i18n'

const ICONS: Record<string, ReactNode> = {
  search: <Search />,
  archive: <Archive />,
  snooze: <Clock />,
  labels: <Tag />,
  help: <HelpCircle />,
}

export function DoneStep() {
  const g = useGuide()
  const me = useMe().data!
  const [tip, setTip] = useState<number | null>(null)
  const name = firstName(me.user.name)
  if (tip === null) {
    return (
      <StepFrame
        icon={<PartyPopper />}
        title={name ? tr('onboarding.steps.done.youreAllSet', { itemName: name }) : tr('onboarding.steps.done.youreAllSet2')}
        lead={tr('onboarding.steps.done.wantAQuickTourFive')}
        primary={{ label: tr('onboarding.steps.done.showMeAround'), onClick: () => setTip(0) }}
        skip
        skipLabel={tr('onboarding.steps.done.goToMyInbox')}
      >
        {g.state.offer.notes !== false && <NotesIntro />}
        <p className="ob-hint ob-center">{tr('onboarding.steps.done.youCanOpenThisGuide')}</p>
      </StepFrame>
    )
  }
  const t = TIPS[tip]
  const last = tip === TIPS.length - 1
  return (
    <StepFrame
      icon={ICONS[t.id]}
      title={withAssistantName(t.title)}
      lead={withAssistantName(t.body)}
      primary={{ label: last ? tr('onboarding.steps.done.goToMyInbox') : tr('onboarding.steps.done.nextTip'), onClick: () => (last ? g.finish() : setTip(tip + 1)) }}
      skip={!last}
      skipLabel={tr('onboarding.steps.done.skipTheTour')}
    >
      <div className="ob-tip-dots" aria-label={tr('onboarding.steps.done.tipOf', { value: tip + 1, tipsLength: TIPS.length })} data-testid="tour">
        {TIPS.map((x, i) => (
          <span key={x.id} className={i === tip ? 'on' : ''} />
        ))}
      </div>
    </StepFrame>
  )
}

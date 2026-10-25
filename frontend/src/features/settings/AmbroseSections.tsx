// Settings → Ambrose → Briefs & replies: where a standing order's brief sits in a conversation,
// and the short reply suggestions under personal mail. Both save at once with Undo.

import { Radio, Section, SetRow } from './common'
import { useInstantSettings } from './save'
import { t } from '../../i18n'

export function BriefsSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('settings.ambroseSections.briefsAndReplySuggestions')}>
      {/* ACT brief.dock @web.settings */}
      <SetRow k={t('settings.ambroseSections.briefs')} sub={t('settings.ambroseSections.theReportAStandingOrder')}>
        <Radio name="brief" value="top" current={s.brief_position ?? 'top'} onChange={(v) => set('brief_position', v)} label={t('settings.ambroseSections.aboveTheMessages')} />
        <Radio name="brief" value="side" current={s.brief_position ?? 'top'} onChange={(v) => set('brief_position', v)} label={t('settings.ambroseSections.besideTheMessages')} desc={t('settings.ambroseSections.staysInViewWhileYou')} />
      </SetRow>
      {/* ACT mail.reply_suggestions @web.settings */}
      <SetRow k={t('settings.ambroseSections.replySuggestions')} sub={t('settings.ambroseSections.threeShortRepliesUnderA')}>
        <Radio name="rs" value="on" current={s.reply_suggestions === false ? 'off' : 'on'} onChange={() => set('reply_suggestions', true)} label={t('settings.ambroseSections.showReplySuggestions')} />
        <Radio name="rs" value="off" current={s.reply_suggestions === false ? 'off' : 'on'} onChange={() => set('reply_suggestions', false)} label={t('settings.ambroseSections.dontShowThem')} />
      </SetRow>
    </Section>
  )
}

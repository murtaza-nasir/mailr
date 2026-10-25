// Settings → Look: the style (the five layouts), light or dark, text and spacing, what only the
// current layout offers, and keyboard shortcuts. The names follow the setup guide's Look step:
// "Style" for the layout, "Light or dark" for the colour mode.

import { ColoursPicker } from './ColoursPicker'
import { ThemePicker } from '../../themes/ThemePicker'
import { useThemeDef } from '../../themes/useUITheme'
import { Radio, Section, SetRow } from './common'
import { useInstantSettings } from './save'
import { t } from '../../i18n'

export function StyleSection() {
  return (
    <Section title={t('common.style')} desc={t('settings.lookSections.howTheWholeAppIs')}>
      <ThemePicker undoable colours={false} />
    </Section>
  )
}

/** Colours: the style's presets, or a colour of your own. */
export function ColoursSection() {
  const theme = useThemeDef()
  return (
    <Section title={t('colour.picker.title')} desc={t('colour.picker.desc', { themeName: theme.name })}>
      <ColoursPicker undoable />
    </Section>
  )
}

export function LightDarkSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('common.lightOrDark')}>
      <div className="row" role="radiogroup" aria-label={t('common.lightOrDark')} style={{ flexWrap: 'wrap', gap: 16 }}>
        <Radio name="theme" value="system" current={s.theme} onChange={(v) => set('theme', v)} label={t('common.automatic')} desc={t('settings.lookSections.followsYourDevice')} />
        <Radio name="theme" value="light" current={s.theme} onChange={(v) => set('theme', v)} label={t('common.light')} />
        <Radio name="theme" value="dark" current={s.theme} onChange={(v) => set('theme', v)} label={t('common.dark')} />
      </div>
    </Section>
  )
}

export function TextSpacingSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('common.textAndSpacing')} desc={t('settings.lookSections.theMailListSidebarToolbars')}>
      <div className="row" role="radiogroup" aria-label={t('common.textAndSpacing')} style={{ flexWrap: 'wrap', gap: 16 }}>
        <Radio name="den" value="comfortable" current={s.density} onChange={(v) => set('density', v)} label={t('common.roomy')} desc={t('settings.lookSections.biggerEasierToRead')} />
        <Radio name="den" value="default" current={s.density} onChange={(v) => set('density', v)} label={t('common.standard')} desc={t('settings.lookSections.aGoodBalance')} />
        <Radio name="den" value="compact" current={s.density} onChange={(v) => set('density', v)} label={t('common.compact')} desc={t('settings.lookSections.moreOnTheScreen')} />
      </div>
    </Section>
  )
}

/** What only the current style offers: its reading pane, and the Cards bundles. */
export function ThisLayoutSection() {
  const { s, set } = useInstantSettings()
  const theme = useThemeDef()
  const PANES = [['none', t('settings.lookSections.noSplit')], ['right', t('settings.lookSections.rightOfInbox')], ['below', t('settings.lookSections.belowInbox')]] as const
  const fixedPane = !!theme.readingPane && !theme.paneChoices
  const choices = theme.paneChoices ?? PANES.map(([v]) => v)
  const byTheme = s.reading_pane_by_theme ?? {}
  const current = theme.readingPane ? (byTheme[theme.id] ?? theme.readingPane(true)) : s.reading_pane
  const choose = (v: 'none' | 'right' | 'below') =>
    theme.readingPane ? set('reading_pane_by_theme', { ...byTheme, [theme.id]: v }) : set('reading_pane', v)
  return (
    <Section title={t('settings.lookSections.thisLayout', { themeName: theme.name })} desc={t('settings.lookSections.choicesThatOnlyThisStyle')}>
      <SetRow k={t('settings.lookSections.readingPane')} sub={fixedPane ? t('settings.lookSections.placesTheReadingPaneItself', { themeName: theme.name }) : t('settings.lookSections.whereAConversationOpens')}>
        {!fixedPane && PANES.filter(([v]) => choices.includes(v)).map(([v, l]) => <Radio key={v} name="rp" value={v} current={current} onChange={choose} label={l} />)}
      </SetRow>
      {theme.id === 'cards' && (
        <SetRow k={t('settings.lookSections.bundles')} sub={t('settings.lookSections.cardsGroupsMailIntoPeople')}>
          {([
            ['flat', t('settings.lookSections.whenTheInboxHasNo'), t('settings.lookSections.sectionsFromTheInboxType')],
            ['always', t('settings.lookSections.alwaysAlsoInsideSections'), undefined],
            ['never', t('common.never'), t('settings.lookSections.aPlainListOfCards')],
          ] as const).map(([v, l, d]) => (
            <Radio key={v} name="cb" value={v} current={s.card_bundles ?? 'flat'} onChange={(x) => set('card_bundles', x)} label={l} desc={d} />
          ))}
        </SetRow>
      )}
    </Section>
  )
}

export function KeyboardSection() {
  const { s, set } = useInstantSettings()
  return (
    <Section title={t('common.keyboardShortcuts')} desc={t('settings.lookSections.pressAnywhereToSeeThem')}>
      <Radio name="kb" value="on" current={s.keyboard_shortcuts ? 'on' : 'off'} onChange={() => set('keyboard_shortcuts', true)} label={t('settings.lookSections.keyboardShortcutsOn')} />
      <Radio name="kb" value="off" current={s.keyboard_shortcuts ? 'on' : 'off'} onChange={() => set('keyboard_shortcuts', false)} label={t('settings.lookSections.keyboardShortcutsOff')} />
    </Section>
  )
}

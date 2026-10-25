// Step 3: the look. Three recommended styles with live previews (the rest behind "More styles"),
// light, dark or automatic, and how much fits on the screen. Every choice applies at once.

import { Monitor, Moon, Palette, Sun } from 'lucide-react'
import { useState } from 'react'
import { useSettings, useUpdateSettings } from '../../../api/hooks'
import type { Settings } from '../../../api/types'
import { THEMES } from '../../../themes/registry'
import type { ThemeId } from '../../../themes/types'
import { useSetTheme, useThemeId } from '../../../themes/useUITheme'
import { ChoiceCard, StepFrame } from '../guide'
import { t as tr } from '../../../i18n'

/** The three styles the guide recommends, in plain words. */
export const RECOMMENDED: { id: ThemeId; blurb: string }[] = [
  { id: 'classic', blurb: tr('onboarding.steps.look.roomyWithLabelsInA') },
  { id: 'cards', blurb: tr('onboarding.steps.look.friendlyAndColourfulBuiltAround') },
  { id: 'office', blurb: tr('onboarding.steps.look.foldersARibbonAndA') },
]
const MORE: { id: ThemeId; blurb: string }[] = [
  { id: 'focus', blurb: tr('onboarding.steps.look.oneCalmColumnForKeyboard') },
  { id: 'desk', blurb: tr('onboarding.steps.look.aDenseTableForA') },
]

const SCHEMES: { id: Settings['theme']; label: string; icon: typeof Sun }[] = [
  { id: 'light', label: tr('common.light'), icon: Sun },
  { id: 'dark', label: tr('common.dark'), icon: Moon },
  { id: 'system', label: tr('common.automatic'), icon: Monitor },
]

const DENSITIES: { id: Settings['density']; label: string; desc: string }[] = [
  { id: 'comfortable', label: tr('common.roomy'), desc: tr('onboarding.steps.look.biggerEasierToRead') },
  { id: 'default', label: tr('common.standard'), desc: tr('onboarding.steps.look.aGoodBalance') },
  { id: 'compact', label: tr('common.compact'), desc: tr('onboarding.steps.look.moreMailOnTheScreen') },
]

export function LookStep() {
  const current = useThemeId()
  const setTheme = useSetTheme()
  const s = useSettings()
  const upd = useUpdateSettings()
  const [more, setMore] = useState(() => MORE.some((m) => m.id === current))
  const list = more ? [...RECOMMENDED, ...MORE] : RECOMMENDED
  return (
    <StepFrame icon={<Palette />} title={tr('onboarding.steps.look.chooseYourLook')} lead={tr('onboarding.steps.look.pickWhatFeelsRightYou')} wide>
      <div className="ob-themes" role="radiogroup" aria-label={tr('common.style')}>
        {list.map(({ id, blurb }) => {
          const t = THEMES[id]
          return (
            <ChoiceCard key={id} on={current === id} onSelect={() => setTheme(id, false)} title={t.name} desc={blurb} testId={`ob-theme-${id}`} name={t.name}>
              <t.Preview />
            </ChoiceCard>
          )
        })}
      </div>
      {!more && (
        <button type="button" className="btn text" onClick={() => setMore(true)}>
          {tr('onboarding.steps.look.moreStyles')}
        </button>
      )}
      <div className="ob-row-group">
        <span className="ob-label" id="ob-scheme">
          {tr('common.lightOrDark')}
        </span>
        <div className="ob-segmented" role="radiogroup" aria-labelledby="ob-scheme">
          {SCHEMES.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" role="radio" aria-checked={s.theme === id} className={s.theme === id ? 'on' : ''} onClick={() => upd.mutate({ theme: id })}>
              <Icon /> {label}
            </button>
          ))}
        </div>
        {s.theme === 'system' && <span className="ob-hint">{tr('onboarding.steps.look.followsYourDeviceLightBy')}</span>}
      </div>
      <div className="ob-row-group">
        <span className="ob-label" id="ob-density">
          {tr('common.textAndSpacing')}
        </span>
        <div className="ob-segmented" role="radiogroup" aria-labelledby="ob-density">
          {DENSITIES.map((d) => (
            <button key={d.id} type="button" role="radio" aria-checked={s.density === d.id} className={s.density === d.id ? 'on' : ''} onClick={() => upd.mutate({ density: d.id })} title={d.desc}>
              {d.label}
            </button>
          ))}
        </div>
      </div>
    </StepFrame>
  )
}

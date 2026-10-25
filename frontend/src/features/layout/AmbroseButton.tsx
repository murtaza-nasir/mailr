// Ambrose's one button on desktop. Every theme draws it in the
// same place: the first item of the top bar's right-hand group, left of the bell. The place is this
// component and its `.ambrose-btn` style (styles/layout.css), not each shell's own code, so moving
// Ambrose means changing one file. On phones the bottom bar carries Ambrose (far right), so the
// top-bar button hides there (html[data-phone]).
import { Bot } from 'lucide-react'
import { useUI } from '../../stores/ui'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { t } from '../../i18n'

/** The app icon every switcher uses for Ambrose (web themes, the phone bar). */
export const AmbroseIcon = Bot

export function AmbroseButton() {
  const name = useAssistantName()
  const panel = useUI((s) => s.panel)
  const on = panel === 'agent'
  return (
    <button
      type="button"
      className={`ambrose-btn no-print${on ? ' active' : ''}`}
      aria-pressed={on}
      aria-label={name}
      data-tip={t('layout.ambroseButton.ctrl', { name })}
      data-ambrose=""
      onClick={() => useUI.getState().setPanel(on ? null : 'agent')}
    >
      <AmbroseIcon aria-hidden />
      <span className="ambrose-name">{name}</span>
    </button>
  )
}

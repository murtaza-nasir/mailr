import { useSettings } from '../../api/hooks'
import { useMailRoute } from '../../features/layout/mailboxes'
import type { ShortcutScope } from '../../lib/shortcuts'
import { CALENDAR, CONV, LIST, NOTES, RECORDINGS, SELECTED, TASKS, hintsFor } from './hints'
import { useUI } from '../../stores/ui'
import { t, tx } from '../../i18n'

/** A quiet strip of the keys that matter here. */
export function KeyHints() {
  const { convId, isMail, app } = useMailRoute()
  const shortcuts = useSettings().keyboard_shortcuts
  const any = useUI((s) => s.selected.size > 0)
  if (!isMail && app !== 'calendar' && app !== 'tasks' && app !== 'recordings' && app !== 'notes') return null
  const scope: ShortcutScope = app === 'calendar' || app === 'tasks' || app === 'recordings' || app === 'notes' ? app : 'mail'
  const keys = !shortcuts ? [] : hintsFor(scope, app === 'calendar' ? CALENDAR : app === 'tasks' ? TASKS : app === 'recordings' ? RECORDINGS : app === 'notes' ? NOTES : any ? SELECTED : convId ? CONV : LIST)
  return (
    <div className="f-hints no-print" aria-label={t('common.keyboardShortcuts')} data-hints={app}>
      {keys.map(([k, what]) => (
        <span key={what}>
          {k.map((p, i) => (
            <kbd key={i}>{p}</kbd>
          ))}
          {what}
        </span>
      ))}
      <span>
        {tx('themes.focus.keyHints.ctrlKCommands', undefined, { kbd: (c) => <kbd>{c}</kbd> })}
      </span>
      {shortcuts && (
        <span>
          {tx('themes.focus.keyHints.allKeys', undefined, { kbd: (c) => <kbd>{c}</kbd> })}
        </span>
      )}
    </div>
  )
}

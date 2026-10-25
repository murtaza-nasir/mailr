import { withAssistantName } from '../rail/assistant/useAssistantName'
import { useMemo } from 'react'
import { Link, useLocation } from 'react-router'
import { useSettings } from '../../api/hooks'
import { Dialog } from '../../components/Dialog'
import { HELP_ONLY, NOTE_EDITOR_HELP, bindingsFor, displayKeys, scopeForPath, shortcutGroupLabel, type Binding, type ShortcutGroup } from '../../lib/shortcuts'
import { useUI } from '../../stores/ui'
import { t, tx } from '../../i18n'

const TITLES = { mail: t('common.keyboardShortcuts'), calendar: t('shortcuts.helpOverlay.calendarShortcuts'), tasks: t('shortcuts.helpOverlay.tasksShortcuts'), recordings: t('shortcuts.helpOverlay.recordingsShortcuts'), notes: t('shortcuts.helpOverlay.notesShortcuts'), settings: t('common.keyboardShortcuts') } as const

/** `?`: the keys of the app you are in (mail, calendar, tasks, recordings or notes), then the keys that work everywhere
 * (in Settings, only those). */
export function HelpOverlay() {
  const open = useUI((s) => s.helpOpen)
  const setHelp = useUI((s) => s.setHelp)
  const settings = useSettings()
  const scope = scopeForPath(useLocation().pathname)
  const groups = useMemo(() => {
    // One row per action: keys that do the same thing share it ("j / n / →").
    const rows = new Map<string, { keys: string[]; b: Binding }>()
    for (const b of bindingsFor(scope)) {
      if (b.noHelp) continue
      const id = `${b.group}|${b.action}|${b.description}`
      const row = rows.get(id)
      if (row) row.keys.push(b.helpKeys ?? b.keys)
      else rows.set(id, { keys: [b.helpKeys ?? b.keys], b })
    }
    // Keys other code handles (the palette, the editor's formatting), listed too.
    for (const h of HELP_ONLY)
      rows.set(`${h.group}|help|${h.description}`, { keys: [h.keys], b: { keys: h.keys, action: 'help', description: h.description, group: h.group } })
    // Notes: the keys of an open note, which the editor handles itself.
    if (scope === 'notes')
      for (const h of NOTE_EDITOR_HELP)
        rows.set(`In an open note|help|${h.description}`, { keys: [h.keys], b: { keys: h.keys, action: 'help', description: h.description, group: 'In an open note' } })
    const m = new Map<string, { keys: string[]; b: Binding }[]>()
    for (const r of rows.values()) m.set(r.b.group, [...(m.get(r.b.group) ?? []), r])
    return [...m.entries()]
    // The tables can grow at run time (registerBindings): recompute whenever help opens.
  }, [scope, open]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={open} onClose={() => setHelp(false)} title={TITLES[scope]} wide>
      {!settings.keyboard_shortcuts && (
        <div className="banner warn" style={{ marginBottom: 16 }}>
          {tx('shortcuts.helpOverlay.keyboardShortcutsAreOffTurn', undefined, { link: (c) => <Link to="/settings/look/look#keyboard" onClick={() => setHelp(false)}>{c}</Link> })}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 24 }} data-help-scope={scope}>
        {groups.map(([group, list]) => (
          <section key={group}>
            <h3 style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>{shortcutGroupLabel(group as ShortcutGroup)}</h3>
            <table className="table">
              <tbody>
                {list.map(({ keys, b }) => (
                  <tr key={keys.join('|')}>
                    <td style={{ width: 130, whiteSpace: 'nowrap' }}>
                      {keys.map((k, j) => (
                        <span key={k}>
                          {j > 0 && <span className="muted"> / </span>}
                          {displayKeys(k)
                            .split(' then ')
                            .map((part, i) => (
                              <span key={i}>
                                {i > 0 && <span className="muted">{' '}{t('shortcuts.helpOverlay.then')}{' '}</span>}
                                <kbd>{part}</kbd>
                              </span>
                            ))}
                        </span>
                      ))}
                    </td>
                    <td>{withAssistantName(b.description)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      </div>
    </Dialog>
  )
}

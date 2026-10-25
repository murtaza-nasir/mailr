import { Check } from 'lucide-react'
import { THEME_LIST } from './registry'
import { savedToast } from '../features/settings/save'
import { AccentPicker } from './AccentPicker'
import { useDeviceTheme, useSetTheme, useThemeId } from './useUITheme'
import { t as tr } from '../i18n'

/**
 * Pick the UI theme. Switching applies at once (no save button, no reload). By default the
 * choice is saved to the account; "Only on this device" keeps it in this browser instead, so
 * two machines can show different themes side by side.
 */
export function ThemePicker({ compact, undoable, colours = true }: { compact?: boolean; undoable?: boolean; colours?: boolean }) {
  const current = useThemeId()
  const device = useDeviceTheme((s) => s.theme)
  const setDevice = useDeviceTheme((s) => s.setTheme)
  const setTheme = useSetTheme()
  const deviceOnly = device !== null

  return (
    <div className={`theme-picker${compact ? ' compact' : ''}`}>
      <div className="theme-grid" role="radiogroup" aria-label={tr('common.style')}>
        {THEME_LIST.map((t) => {
          const on = t.id === current
          return (
            <button
              key={t.id}
              type="button"
              role="radio"
              aria-checked={on}
              className={`theme-card${on ? ' on' : ''}`}
              onClick={() => {
                if (t.id === current) return
                const was = current
                setTheme(t.id, deviceOnly)
                // Settings → Look offers Undo, as every settings page does.
                if (undoable) savedToast(tr('themes.themePicker.style', { itemName: t.name }), () => setTheme(was, deviceOnly))
              }}
              data-tip={compact ? t.tagline : undefined}
            >
              <span className="thumb">
                <t.Preview />
                {on && (
                  <span className="tick" aria-hidden>
                    <Check />
                  </span>
                )}
              </span>
              <span className="name">{t.name}</span>
              {!compact && <span className="tagline">{t.tagline}</span>}
              {!compact && <span className="desc">{t.description}</span>}
            </button>
          )
        })}
      </div>
      {/* The colours of the chosen style; Settings → Look has them in a section of their own. */}
      {colours && <AccentPicker />}
      <label className="choice device-only">
        <input
          type="checkbox"
          className="checkbox"
          checked={deviceOnly}
          onChange={(e) => {
            if (e.target.checked) setDevice(current)
            else setTheme(current, false)
          }}
        />
        <span>
          {tr('themes.themePicker.onlyOnThisDevice')}
          {!compact && <div className="desc">{tr('themes.themePicker.keepThisChoiceInThis')}</div>}
        </span>
      </label>
    </div>
  )
}

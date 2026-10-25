import { Link } from 'react-router'
import { presetChoice } from '../lib/colour/choice'
import { THEME_COLOURS, type Preset } from '../lib/colour/themes'
import { useAccent, useSetAccent, useThemeDef } from './useUITheme'
import { t } from '../i18n'

/** One preset as a round swatch (a radio in its group). */
export function PresetSwatch({ p, on, onPick }: { p: Preset; on: boolean; onPick: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      aria-label={p.name}
      data-tip={p.name}
      data-preset={p.id}
      className={`accent-swatch${on ? ' on' : ''}`}
      style={{ ['--swatch' as string]: p.seed }}
      onClick={onPick}
    />
  )
}

/**
 * The active style's colour presets under the style picker (quick settings, the Workspace ribbon).
 * Custom links to Settings → Look → Colours, where the wheel is (features/settings/ColoursPicker.tsx).
 */
export function AccentPicker() {
  const def = useThemeDef()
  const resolved = useAccent()
  const save = useSetAccent()
  const isCustom = resolved.preset === null
  return (
    <div className="accent-row" role="radiogroup" aria-label={t('themes.themePicker.accent', { defName: def.name })}>
      <span className="accent-label">{t('themes.themePicker.accent2')}</span>
      {THEME_COLOURS[def.id].presets.map((p) => (
        <PresetSwatch key={p.id} p={p} on={resolved.preset?.id === p.id} onPick={() => save(presetChoice(p))} />
      ))}
      <Link
        className={`accent-swatch custom${isCustom ? ' on' : ''}`}
        to="/settings/look/look#colours"
        role="radio"
        aria-checked={isCustom}
        aria-label={t('colour.picker.custom')}
        data-tip={t('colour.picker.customInSettings')}
      />
    </div>
  )
}

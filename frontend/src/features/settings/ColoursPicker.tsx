import { Hct } from '@material/material-color-utilities'
import { Pencil, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { schemeFor, themeVars } from '../../lib/colour/apply'
import { showAccent } from '../../lib/colour/boot'
import { presetChoice, resolveChoice, type AccentChoice } from '../../lib/colour/choice'
import { hexFromArgb, isHex } from '../../lib/colour/contrast'
import { HARMONIES, harmonySeeds, hctOf, type Harmony, type HueChroma } from '../../lib/colour/engine'
import { THEME_COLOURS } from '../../lib/colour/themes'
import { savedToast } from './save'
import { ColourWheel, MAX_CHROMA } from '../../themes/ColourWheel'
import { PresetSwatch } from '../../themes/AccentPicker'
import { useAccent, useAccentChoice, useSetAccent, useThemeDef } from '../../themes/useUITheme'
import { t } from '../../i18n'

const TONE_MIN = 20
const TONE_MAX = 60

export function harmonyName(h: Harmony): string {
  switch (h) {
    case 'single':
      return t('colour.picker.harmonySingle')
    case 'complementary':
      return t('colour.picker.harmonyComplementary')
    case 'analogous':
      return t('colour.picker.harmonyAnalogous')
    case 'triadic':
      return t('colour.picker.harmonyTriadic')
    case 'split':
      return t('colour.picker.harmonySplit')
  }
}

/**
 * Settings → Look → Colours: the active style's presets as swatches,
 * and Custom, which opens a colour wheel with a harmony and a preview in light and dark. The
 * swatches alone sit under the style picker in quick settings (themes/AccentPicker.tsx).
 */
export function ColoursPicker({ undoable }: { undoable?: boolean }) {
  const def = useThemeDef()
  const theme = THEME_COLOURS[def.id]
  const resolved = useAccent()
  const choice = useAccentChoice()
  const save = useSetAccent()
  const isCustom = resolved.preset === null
  const [open, setOpen] = useState(isCustom)
  // Opening Custom from a preset starts the wheel at that preset's colour.
  const [draft, setDraft] = useState<{ seed: string; harmony: Harmony }>({ seed: resolved.seed, harmony: resolved.harmony })
  useEffect(() => {
    if (isCustom) setDraft({ seed: resolved.seed, harmony: resolved.harmony })
  }, [isCustom, resolved.seed, resolved.harmony])

  // ACT settings.colours_preset @web.settings
  const choose = (next: AccentChoice | null, message?: string) => {
    const was = choice
    save(next)
    if (undoable && message) savedToast(message, () => save(was))
  }

  // ACT settings.colours_custom @web.settings
  // A drag shows at once; the account is saved when the dot rests (a quarter of a second).
  const timer = useRef<number | undefined>(undefined)
  const setCustom = (seed: string, harmony: Harmony) => {
    setDraft({ seed, harmony })
    showAccent(def.id, { seed, harmony })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => save({ seed, harmony }), 250)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])

  return (
    <div className="accent-picker">
      <div className="accent-head">
        <div className="accent-row" role="radiogroup" aria-label={t('themes.themePicker.accent', { defName: def.name })}>
          <span className="accent-label">{t('themes.themePicker.accent2')}</span>
          {theme.presets.map((p) => (
            <PresetSwatch key={p.id} p={p} on={resolved.preset?.id === p.id} onPick={() => {
              setOpen(false)
              if (resolved.preset?.id !== p.id) choose(presetChoice(p), t('colour.picker.savedPreset', { name: p.name }))
            }} />
          ))}
          <button
            type="button"
            role="radio"
            aria-checked={isCustom}
            aria-expanded={open}
            aria-label={t('colour.picker.custom')}
            data-tip={t('colour.picker.custom')}
            className={`accent-swatch custom${isCustom ? ' on' : ''}`}
            onClick={() => {
              // Custom starts from the colour on show, then follows the wheel.
              if (!isCustom) setCustom(resolved.seed, resolved.harmony)
              setOpen(true)
            }}
          />
        </div>
        {(choice !== null || isCustom) && (
          <button type="button" className="btn text sm accent-reset" onClick={() => {
            setOpen(false)
            choose(null, t('colour.picker.savedReset', { name: theme.presets[0].name }))
          }}>
            <RotateCcw /> {t('colour.picker.reset')}
          </button>
        )}
      </div>
      {(open || isCustom) && <CustomEditor seed={draft.seed} harmony={draft.harmony} onChange={setCustom} />}
      <AccentPreview choice={open || isCustom ? draft : choice} />
    </div>
  )
}

function CustomEditor({ seed, harmony, onChange }: { seed: string; harmony: Harmony; onChange: (seed: string, harmony: Harmony) => void }) {
  const hct = hctOf(seed)
  const toneNow = Math.round(Math.max(TONE_MIN, Math.min(TONE_MAX, hct.tone)))
  const [hex, setHex] = useState(seed)
  useEffect(() => setHex(seed), [seed])
  const at = (p: HueChroma, tone: number) => hexFromArgb(Hct.from(p.hue, p.chroma, tone).toInt())
  const partners = harmonySeeds({ hue: hct.hue, chroma: hct.chroma }, harmony)
  // Each partner where it really lands at this tone (a colour out of reach comes in to the edge).
  const marker = (p: HueChroma, label: string) => {
    const h = Hct.from(p.hue, p.chroma, toneNow)
    return { hue: p.hue, chroma: h.chroma, label, color: hexFromArgb(h.toInt()) }
  }
  const markers = [marker(partners.secondary, t('colour.picker.secondary')), marker(partners.tertiary, t('colour.picker.tertiary'))]
  const valid = isHex(hex)
  return (
    <div className="accent-custom" data-testid="accent-custom">
      <ColourWheel
        value={{ hue: hct.hue, chroma: Math.min(hct.chroma, MAX_CHROMA) }}
        tone={toneNow}
        markers={markers}
        label={t('colour.picker.wheel')}
        onChange={(p) => onChange(at(p, toneNow), harmony)}
      />
      <div className="accent-controls">
        <label className="field">
          <span className="field-label">{t('colour.picker.tone')}</span>
          <input
            type="range"
            min={TONE_MIN}
            max={TONE_MAX}
            value={toneNow}
            aria-valuetext={t('colour.picker.toneValue', { tone: toneNow })}
            onChange={(e) => onChange(at({ hue: hct.hue, chroma: hct.chroma }, Number(e.target.value)), harmony)}
          />
        </label>
        <label className="field">
          <span className="field-label">{t('colour.picker.hex')}</span>
          <input
            className="input accent-hex"
            value={hex}
            spellCheck={false}
            maxLength={7}
            aria-invalid={!valid}
            onChange={(e) => {
              const v = e.target.value.trim()
              setHex(v)
              const full = v.startsWith('#') ? v : `#${v}`
              if (isHex(full)) onChange(full.toLowerCase(), harmony)
            }}
          />
        </label>
        <div className="field" role="radiogroup" aria-label={t('colour.picker.harmony')}>
          <span className="field-label">{t('colour.picker.harmony')}</span>
          <div className="accent-harmonies">
            {HARMONIES.map((h) => (
              <label key={h} className="choice">
                <input type="radio" className="radio" name="accent-harmony" value={h} checked={harmony === h} onChange={() => onChange(seed, h)} />
                <span>{harmonyName(h)}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * A live sample of the colours in light and dark side by side: an active folder, a list row
 * (selected), buttons, chips and a Due pill, drawn with the theme's own rules.
 */
export function AccentPreview({ choice }: { choice: AccentChoice | null }) {
  const def = useThemeDef()
  const res = resolveChoice(def.id, choice)
  const vars = useMemo(() => {
    if (res.kind === 'native') return null
    const r = schemeFor(def.id, res.seed, res.harmony)
    return { light: themeVars(def.id, r, 'light'), dark: themeVars(def.id, r, 'dark') }
  }, [def.id, res.kind, res.seed, res.harmony])
  return (
    <div className="accent-preview-pair" aria-label={t('colour.picker.preview')} role="group">
      {(['light', 'dark'] as const).map((scheme) => (
        <div key={scheme} className="accent-preview" data-ui-theme={def.id} data-theme={scheme} data-testid={`accent-preview-${scheme}`} style={(vars?.[scheme] ?? {}) as CSSProperties}>
          <div className="ap-caption">{scheme === 'light' ? t('common.light') : t('common.dark')}</div>
          <div className="ap-body">
            <div className="ap-nav">
              <span className="ap-folder on">
                {t('colour.picker.sampleInbox')} <b>12</b>
              </span>
              <span className="ap-folder">{t('colour.picker.sampleStarred')}</span>
            </div>
            <div className="ap-rows">
              <div className="ap-row on">
                <b>{t('colour.picker.sampleSender')}</b>
                <span className="ap-subject">{t('colour.picker.sampleSubject')}</span>
                <span className="signal-chip ap-chip needs_reply">{t('colour.picker.sampleReply')}</span>
              </div>
              <div className="ap-row">
                <span>{t('colour.picker.sampleSender2')}</span>
                <span className="ap-subject">{t('colour.picker.sampleSubject2')}</span>
                <span className="signal-chip ap-chip deadline">{t('colour.picker.sampleDue')}</span>
              </div>
            </div>
            <div className="ap-actions">
              <span className="ap-btn filled">{t('colour.picker.sampleButton')}</span>
              <span className="ap-btn compose">
                <Pencil /> {t('colour.picker.sampleCompose')}
              </span>
              <span className="ap-filter">{t('colour.picker.sampleFilter')}</span>
              <span className="ap-link">{t('colour.picker.sampleLink')}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

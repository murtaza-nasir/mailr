// The label colour picker: scheme tabs, a grid of hues (columns)
// by shades (rows) drawn as real chips in the current theme, a live preview of the label,
// "Custom…" for any hex colour (text colour chosen for contrast) and "Remove colour".

import { Ban, Pipette } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useSettings } from '../../api/hooks'
import {
  chipStyle,
  contrast,
  DEFAULT_SCHEME,
  describeColor,
  isHexColor,
  parseToken,
  readableText,
  SCHEMES,
  SHADES,
  schemeById,
  tokenOf,
} from '../../lib/labels'
import { t as tr } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

/** The user's label colour scheme setting (Settings → Mail → Labels). */
export function useLabelScheme(): string {
  const s = useSettings() as { label_scheme?: string }
  return schemeById(s.label_scheme ?? DEFAULT_SCHEME).id
}

export function ColorPicker({ value, name = '', onPick }: { value: string | null; name?: string; onPick: (color: string | null) => void }) {
  const userScheme = useLabelScheme()
  const current = parseToken(value)
  const [schemeId, setSchemeId] = useState(current?.scheme ?? userScheme)
  const scheme = schemeById(schemeId)
  const [preview, setPreview] = useState<string | null>(null)
  const [pos, setPos] = useState(() => {
    const h = current ? scheme.hues.findIndex((x) => x.id === current.hue) : 0
    return { hue: Math.max(0, h), shade: current ? current.shade - 1 : 0 }
  })
  const [custom, setCustom] = useState(isHexColor(value))
  const [hex, setHex] = useState(() => (isHexColor(value) ? value!.split(/[,|]/)[0] : '#3b6a8f'))
  const cells = useRef<(HTMLButtonElement | null)[]>([])
  const cols = scheme.hues.length

  // Focus goes to the chosen colour (or the first cell) once the popover has opened.
  useEffect(() => {
    const t = window.setTimeout(() => cells.current[pos.shade * cols + pos.hue]?.focus({ preventScroll: true }), 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const move = (hue: number, shade: number) => {
    const h = (hue + cols) % cols
    const s = Math.min(SHADES - 1, Math.max(0, shade))
    setPos({ hue: h, shade: s })
    cells.current[s * cols + h]?.focus()
  }
  const onGridKey = (e: KeyboardEvent) => {
    const k = e.key
    const step: Record<string, [number, number]> = { ArrowRight: [arrowStep('ArrowRight'), 0], ArrowLeft: [arrowStep('ArrowLeft'), 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }
    if (step[k]) move(pos.hue + step[k][0], pos.shade + step[k][1])
    else if (k === 'Home') move(0, e.ctrlKey ? 0 : pos.shade)
    else if (k === 'End') move(cols - 1, e.ctrlKey ? SHADES - 1 : pos.shade)
    else return
    e.preventDefault()
    e.stopPropagation()
  }
  const onTabKey = (e: KeyboardEvent, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
    e.preventDefault()
    const next = SCHEMES[(i + arrowStep(e.key) + SCHEMES.length) % SCHEMES.length]
    setSchemeId(next.id)
    document.getElementById(`lcp-tab-${next.id}`)?.focus()
  }

  const validHex = /^#[0-9a-f]{6}$/i.test(hex)
  const customValue = validHex ? hex.toLowerCase() : null
  const shown = preview ?? (custom && customValue ? customValue : value)
  const fg = validHex ? readableText(hex) : '#000000'

  return (
    <div className="lcp" onMouseLeave={() => setPreview(null)}>
      <div className="lcp-preview" aria-live="polite">
        <span className="label-chip lg" style={chipStyle(shown)} data-testid="lcp-preview">
          {name ? name.split('/').pop() : tr('common.label')}
        </span>
        <span className="lcp-desc">{describeColor(shown)}</span>
      </div>
      <div className="lcp-tabs" role="tablist" aria-label={tr('mail.labelColourPicker.colourScheme')}>
        {SCHEMES.map((s, i) => (
          <button
            key={s.id}
            id={`lcp-tab-${s.id}`}
            type="button"
            role="tab"
            aria-selected={s.id === scheme.id}
            tabIndex={s.id === scheme.id ? 0 : -1}
            title={s.description}
            onClick={() => setSchemeId(s.id)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {s.name}
            {s.id === userScheme && <span className="lcp-yours" aria-label={tr('mail.labelColourPicker.yourScheme')}> ●</span>}
          </button>
        ))}
      </div>
      <div className="lcp-grid" role="grid" aria-label={tr('mail.labelColourPicker.colours', { schemeName: scheme.name })} onKeyDown={onGridKey} style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
        {Array.from({ length: SHADES }, (_, s) => (
          <div role="row" key={s} className="lcp-row">
            {scheme.hues.map((h, hi) => {
              const tok = tokenOf(scheme.id, h.id, s + 1)
              const selected = value === tok
              return (
                <div role="gridcell" key={h.id}>
                  <button
                    type="button"
                    ref={(el) => {
                      cells.current[s * cols + hi] = el
                    }}
                    className="lcp-cell label-chip"
                    style={chipStyle(tok)}
                    aria-label={tr('mail.labelColourPicker.shade', { itemName: h.name, value: s + 1 })}
                    aria-pressed={selected}
                    tabIndex={pos.hue === hi && pos.shade === s ? 0 : -1}
                    data-tip={tr('mail.labelColourPicker.shade', { itemName: h.name, value: s + 1 })}
                    onMouseEnter={() => setPreview(tok)}
                    onFocus={() => {
                      setPreview(tok)
                      setPos({ hue: hi, shade: s })
                    }}
                    onClick={() => onPick(tok)}
                  >
                    a
                  </button>
                </div>
              )
            })}
          </div>
        ))}
      </div>
      {custom && (
        <div className="lcp-custom">
          <input type="color" aria-label={tr('mail.labelColourPicker.customColour')} value={validHex ? hex : '#000000'} onChange={(e) => setHex(e.target.value)} />
          <input
            className="input"
            aria-label={tr('mail.labelColourPicker.customColourHex')}
            value={hex}
            maxLength={7}
            onChange={(e) => setHex(e.target.value.startsWith('#') ? e.target.value : `#${e.target.value}`)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && customValue) onPick(customValue)
            }}
          />
          <span className="lcp-contrast muted">{validHex ? tr('mail.labelColourPicker.text1', { value: fg === '#ffffff' ? tr('mail.labelColourPicker.white') : tr('mail.labelColourPicker.black'), contrast: contrast(hex, fg).toFixed(1) }) : tr('mail.labelColourPicker.enterRrggbb')}</span>
          <button type="button" className="btn tonal sm" disabled={!customValue} onClick={() => customValue && onPick(customValue)}>
            {tr('mail.labelColourPicker.use')}
          </button>
        </div>
      )}
      <div className="lcp-foot">
        <button type="button" className="btn text sm" onClick={() => setCustom((c) => !c)} aria-expanded={custom}>
          <Pipette />{' '}{tr('common.custom')}
        </button>
        <button type="button" className="btn text sm" onClick={() => onPick(null)} disabled={!value}>
          <Ban />{' '}{tr('mail.labelColourPicker.removeColour')}
        </button>
      </div>
    </div>
  )
}

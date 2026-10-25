import { Hct } from '@material/material-color-utilities'
import { useEffect, useRef, type KeyboardEvent, type PointerEvent } from 'react'
import { hexFromArgb } from '../lib/colour/contrast'
import type { HueChroma } from '../lib/colour/engine'
import { t } from '../i18n'

/** The wheel's outer edge, in HCT chroma. Few colours reach further. */
export const MAX_CHROMA = 120
const SIZE = 220
const R = SIZE / 2 - 6

/** Position on the wheel: hue around (0 degrees at the right, counter-clockwise), chroma outward. */
function pos(p: HueChroma): [number, number] {
  const r = (Math.min(p.chroma, MAX_CHROMA) / MAX_CHROMA) * R
  const a = (p.hue * Math.PI) / 180
  return [SIZE / 2 + r * Math.cos(a), SIZE / 2 - r * Math.sin(a)]
}

export interface WheelMarker extends HueChroma {
  label: string
  color: string
}

/**
 * A hue/chroma wheel at one tone. Drag the main dot or use the arrow keys: Left and Right turn
 * the hue, Up and Down change how colourful it is (Shift for bigger steps). The partner markers
 * follow the main dot (the harmony places them).
 */
export function ColourWheel({ value, tone, markers, onChange, label }: {
  value: HueChroma
  tone: number
  markers: WheelMarker[]
  onChange: (v: HueChroma) => void
  label: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const dragging = useRef(false)

  useEffect(() => {
    const c = canvas.current
    const ctx = c?.getContext('2d')
    if (!c || !ctx) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const n = Math.round(SIZE * dpr)
    c.width = c.height = n
    // A table of the colours HCT gives at this tone (2 degrees by 2.5 chroma; out of gamut, the
    // nearest colour that exists), then every pixel takes its nearest entry.
    const HUES = 180
    const RINGS = 48
    const table = new Uint32Array(HUES * RINGS)
    for (let h = 0; h < HUES; h++) {
      for (let r = 0; r < RINGS; r++) {
        // Out of reach at this tone, the nearest colour that exists (the dot stops at its edge).
        table[h * RINGS + r] = Hct.from(h * 2, (r / RINGS) * MAX_CHROMA, tone).toInt()
      }
    }
    const img = ctx.createImageData(n, n)
    const radius = (R / SIZE) * n
    const mid = n / 2
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        const dx = x + 0.5 - mid
        const dy = mid - (y + 0.5)
        const d = Math.hypot(dx, dy)
        if (d > radius + 1) continue
        const hue = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360
        const k = (Math.round(hue / 2) % HUES) * RINGS + Math.min(RINGS - 1, Math.floor((d / radius) * RINGS))
        const argb = table[k]
        const i = (y * n + x) * 4
        img.data[i] = (argb >> 16) & 255
        img.data[i + 1] = (argb >> 8) & 255
        img.data[i + 2] = argb & 255
        // A soft edge.
        img.data[i + 3] = Math.round(255 * (d > radius ? radius + 1 - d : 1))
      }
    }
    ctx.putImageData(img, 0, 0)
  }, [tone])

  const fromPointer = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - box.left) / box.width) * SIZE - SIZE / 2
    const y = SIZE / 2 - ((e.clientY - box.top) / box.height) * SIZE
    const hue = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
    const chroma = Math.min(1, Math.hypot(x, y) / R) * MAX_CHROMA
    onChange({ hue: Math.round(hue), chroma: Math.round(chroma) })
  }

  const onKey = (e: KeyboardEvent) => {
    const big = e.shiftKey
    let { hue, chroma } = value
    if (e.key === 'ArrowLeft') hue += big ? 15 : 3
    else if (e.key === 'ArrowRight') hue -= big ? 15 : 3
    else if (e.key === 'ArrowUp') chroma += big ? 16 : 4
    else if (e.key === 'ArrowDown') chroma -= big ? 16 : 4
    else if (e.key === 'Home') chroma = 0
    else if (e.key === 'End') chroma = MAX_CHROMA
    else return
    e.preventDefault()
    onChange({ hue: Math.round((hue + 360) % 360), chroma: Math.round(Math.max(0, Math.min(MAX_CHROMA, chroma))) })
  }

  const [x, y] = pos(value)
  return (
    <div
      className="colour-wheel"
      style={{ width: SIZE, height: SIZE }}
      onPointerDown={(e) => {
        dragging.current = true
        e.currentTarget.setPointerCapture(e.pointerId)
        fromPointer(e)
        e.currentTarget.querySelector<HTMLElement>('.cw-dot')?.focus()
      }}
      onPointerMove={(e) => dragging.current && fromPointer(e)}
      onPointerUp={() => (dragging.current = false)}
      onPointerCancel={() => (dragging.current = false)}
    >
      <canvas ref={canvas} style={{ width: SIZE, height: SIZE }} aria-hidden />
      {markers.map((m) => {
        const [mx, my] = pos(m)
        // rtl-ok: the wheel is a colour space; hue 0 is on the right in every language.
        return <span key={m.label} className="cw-marker" style={{ left: mx, top: my, background: m.color }} data-tip={m.label} aria-hidden />
      })}
      <span
        className="cw-dot"
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={359}
        aria-valuenow={Math.round(value.hue)}
        aria-valuetext={t('colour.picker.wheelValue', { hue: Math.round(value.hue), chroma: Math.round(value.chroma) })}
        // rtl-ok: a position in the colour space, as above.
        style={{ left: x, top: y, background: hexFromArgb(Hct.from(value.hue, value.chroma, tone).toInt()) }}
        onKeyDown={onKey}
      />
    </div>
  )
}

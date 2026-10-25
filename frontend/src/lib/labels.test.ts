import { describe, expect, it } from 'vitest'
import docsCopy from '../../../spec/fixtures/label-colours.json?raw'
import androidCopy from '../../../android/app/src/test/resources/design/label-colours.json?raw'
import kotlinTable from '../../../android/app/src/main/java/io/github/murtazanasir/mailr/ui/util/LabelColourData.kt?raw'
import data from './labelColours.json'
import {
  chipStyle,
  contrast,
  conversionPlan,
  decodeColor,
  describeColor,
  dotStyle,
  luminance,
  nearestToken,
  oklab,
  parseToken,
  readableText,
  reschemeColor,
  resolveColor,
  SCHEMES,
  SHADES,
} from './labels'

const every = SCHEMES.flatMap((s) => s.hues.flatMap((h) => Array.from({ length: SHADES }, (_, i) => ({ s, h, i }))))
const chroma = (hex: string) => {
  const [, a, b] = oklab(hex)
  return Math.hypot(a, b)
}

describe('label colour schemes', () => {
  it('has six schemes of ten hues by four shades, the hue ids shared', () => {
    expect(SCHEMES.map((s) => s.id)).toEqual(['default', 'muted', 'vivid', 'pastel', 'earth', 'ocean'])
    for (const s of SCHEMES) {
      expect(s.hues.map((h) => h.id)).toEqual(data.hues)
      expect(s.hues.every((h) => h.light.length === 4 && h.dark.length === 4)).toBe(true)
    }
  })

  it('reaches WCAG AA (4.5:1) for chip text in light and dark themes', () => {
    for (const { s, h, i } of every) {
      for (const mode of ['light', 'dark'] as const) {
        const sw = h[mode][i]
        expect(contrast(sw.fg, sw.bg), `${s.id}/${h.id}/${i + 1} ${mode}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('draws light themes as tints with dark text (shade 4 solid) and dark themes as deep backgrounds with light text (Pastel: light tints with dark text)', () => {
    for (const { s, h, i } of every) {
      const lt = h.light[i]
      const dk = h.dark[i]
      const id = `${s.id}/${h.id}/${i + 1}`
      if (i < 3 || s.id === 'pastel') expect(luminance(lt.fg), id).toBeLessThan(luminance(lt.bg))
      if (s.id === 'pastel') {
        expect(luminance(dk.fg), id).toBeLessThan(luminance(dk.bg))
        expect(chroma(dk.bg), `${id} pastel stays soft`).toBeLessThan(0.09)
      } else {
        expect(luminance(dk.fg), id).toBeGreaterThan(luminance(dk.bg))
        // Never neon on dark: a dark-theme background stays deep, and even Vivid stays below sRGB's edge.
        expect(luminance(dk.bg), `${id} dark bg`).toBeLessThan(0.22)
        expect(chroma(dk.bg), `${id} dark chroma`).toBeLessThan(0.22)
      }
    }
  })

  it('steps lightness evenly: each shade is darker (light theme) or lighter (dark theme) than the last', () => {
    for (const s of SCHEMES)
      for (const h of s.hues)
        for (let i = 1; i < SHADES; i++) {
          expect(luminance(h.light[i].bg)).toBeLessThan(luminance(h.light[i - 1].bg))
          // Pastel's dark-theme tints deepen towards shade 4, as in light themes.
          if (s.id === 'pastel') expect(luminance(h.dark[i].bg)).toBeLessThan(luminance(h.dark[i - 1].bg))
          else expect(luminance(h.dark[i].bg)).toBeGreaterThan(luminance(h.dark[i - 1].bg))
        }
  })

  it('keeps every scheme distinct from every other, in light and dark themes', () => {
    // Mean OKLab distance between two schemes' backgrounds, hue by hue and shade by shade;
    // 0.02 is about the smallest difference anyone sees, 0.055 tells two schemes apart at a glance.
    for (const mode of ['light', 'dark'] as const) {
      const all: number[] = []
      for (let a = 0; a < SCHEMES.length; a++)
        for (let b = a + 1; b < SCHEMES.length; b++) {
          const ds = SCHEMES[a].hues.flatMap((h, k) =>
            h[mode].map((sw, i) => {
              const p = oklab(sw.bg)
              const q = oklab(SCHEMES[b].hues[k][mode][i].bg)
              return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])
            }),
          )
          const mean = ds.reduce((x, y) => x + y, 0) / ds.length
          all.push(mean)
          expect(mean, `${SCHEMES[a].id} vs ${SCHEMES[b].id} (${mode})`).toBeGreaterThan(0.055)
        }
      expect(all.reduce((x, y) => x + y, 0) / all.length, `mean over all pairs (${mode})`).toBeGreaterThan(0.09)
    }
  })

  it('gives each scheme its character', () => {
    const meanChroma = (id: string, mode: 'light' | 'dark') => {
      const s = SCHEMES.find((x) => x.id === id)!
      const cs = s.hues.filter((h) => h.id !== 'grey').flatMap((h) => h[mode].map((sw) => chroma(sw.bg)))
      return cs.reduce((x, y) => x + y, 0) / cs.length
    }
    for (const mode of ['light', 'dark'] as const) {
      expect(meanChroma('vivid', mode)).toBeGreaterThan(meanChroma('default', mode))
      expect(meanChroma('default', mode)).toBeGreaterThan(meanChroma('muted', mode))
      expect(meanChroma('muted', mode)).toBeLessThan(0.035)
    }
    // Earth leans warm (positive OKLab b: yellow-brown), Ocean cool (negative b or green-blue a).
    const meanAB = (id: string) => {
      const s = SCHEMES.find((x) => x.id === id)!
      const v = s.hues.flatMap((h) => h.dark.map((sw) => oklab(sw.bg)))
      return [v.reduce((x, y) => x + y[1], 0) / v.length, v.reduce((x, y) => x + y[2], 0) / v.length]
    }
    expect(meanAB('earth')[1]).toBeGreaterThan(0.01)
    expect(meanAB('ocean')[1]).toBeLessThan(0)
    expect(meanAB('ocean')[0]).toBeLessThan(meanAB('default')[0])
  })

  it('keeps the greys grey (the Earth stone a little warm, the Ocean fog a little blue)', () => {
    for (const s of SCHEMES) for (const sw of s.hues.find((h) => h.id === 'grey')!.light) expect(chroma(sw.bg)).toBeLessThan(0.04)
  })
})

describe('token resolution', () => {
  it('resolves a token per mode', () => {
    const r = resolveColor('@default/teal/3')!
    const teal = SCHEMES[0].hues.find((h) => h.id === 'teal')!
    expect(r.light).toEqual(teal.light[2])
    expect(r.dark).toEqual(teal.dark[2])
    expect(decodeColor('@default/teal/3', 'dark')).toEqual({ bg: teal.dark[2].bg, fg: teal.dark[2].fg })
    expect(chipStyle('@default/teal/3')).toEqual({
      background: `light-dark(${teal.light[2].bg}, ${teal.dark[2].bg})`,
      color: `light-dark(${teal.light[2].fg}, ${teal.dark[2].fg})`,
    })
    expect(describeColor('@earth/red/2')).toBe('Terracotta, shade 2 (Earth)')
  })

  it('rejects tokens no scheme has', () => {
    for (const bad of ['@neon/teal/1', '@default/cyan/1', '@default/teal/5', '@default/teal/0', '@default/teal']) {
      expect(parseToken(bad)).toBeNull()
      expect(resolveColor(bad)).toBeNull()
    }
  })

  it('moves a token to another scheme and leaves hex colours alone', () => {
    expect(reschemeColor('@default/teal/3', 'ocean')).toBe('@ocean/teal/3')
    expect(reschemeColor('#16a766,#ffffff', 'ocean')).toBe('#16a766,#ffffff')
  })
})

describe('legacy colours', () => {
  it('reads "#bg,#fg", "#bg|#fg" and "#bg" the same in both themes', () => {
    expect(decodeColor('#fb4c2f,#ffffff')).toEqual({ bg: '#fb4c2f', fg: '#ffffff' })
    expect(decodeColor('#fb4c2f|#ffffff', 'dark')).toEqual({ bg: '#fb4c2f', fg: '#ffffff' })
    expect(decodeColor('#fef1d1')).toEqual({ bg: '#fef1d1', fg: '#000000' })
    expect(decodeColor('#1c4587', 'dark')).toEqual({ bg: '#1c4587', fg: '#ffffff' })
    expect(chipStyle('#16a766,#ffffff')).toEqual({ background: '#16a766', color: '#ffffff' })
    expect(dotStyle('#16a766,#ffffff')).toEqual({ background: '#16a766' })
    expect(decodeColor(null)).toBeNull()
    expect(decodeColor('red')).toBeNull()
  })

  it('chooses readable text for a custom colour', () => {
    expect(readableText('#ffff00')).toBe('#000000')
    expect(readableText('#1a237e')).toBe('#ffffff')
    expect(contrast('#ffffff', '#000000')).toBeCloseTo(21, 1)
  })

  it('converts Gmail colours to the nearest hue and shade', () => {
    expect(parseToken(nearestToken('#fb4c2f', 'default'))?.hue).toBe('red')
    expect(parseToken(nearestToken('#16a766', 'default'))?.hue).toBe('green')
    expect(parseToken(nearestToken('#4a86e8', 'default'))?.hue).toBe('blue')
    expect(parseToken(nearestToken('#a479e2', 'default'))?.hue).toBe('purple')
    expect(parseToken(nearestToken('#ffad47', 'default'))?.hue).toBe('orange')
    expect(nearestToken('#000000', 'default')).toBe('@default/grey/4')
    expect(nearestToken('#f3f3f3', 'default')).toBe('@default/grey/1')
    const pale = parseToken(nearestToken('#c9daf8', 'earth'))!
    expect(pale.scheme).toBe('earth')
    expect(pale.shade).toBeLessThanOrEqual(2)
    const plan = conversionPlan(
      [
        { id: 1, color: '#fb4c2f,#ffffff' },
        { id: 2, color: '@default/teal/3' },
        { id: 3, color: null },
        { id: 4, color: '#16a766' },
      ],
      'default',
    )
    expect(plan.map((p) => p.label.id)).toEqual([1, 4])
  })
})

describe('shared data copies', () => {
  it('the web, docs and Android copies are the same file', () => {
    expect(JSON.parse(docsCopy)).toEqual(data)
    expect(androidCopy).toBe(docsCopy)
  })

  it('the Kotlin table carries the same colours in the same order', () => {
    const shades = [...kotlinTable.matchAll(/Shade\("(#[0-9a-f]{6})", "(#[0-9a-f]{6})", "(#[0-9a-f]{6})", "(#[0-9a-f]{6})", "(#[0-9a-f]{6})", "(#[0-9a-f]{6})"\)/g)].map((m) =>
      m.slice(1).join(' '),
    )
    const want = every.map(({ h, i }) => [h.light[i].bg, h.light[i].fg, h.light[i].dot, h.dark[i].bg, h.dark[i].fg, h.dark[i].dot].join(' '))
    expect(shades).toEqual(want)
  })
})

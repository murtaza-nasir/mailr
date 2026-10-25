// Zones the browser refuses never throw (9 Oct 2026: Outlook's "Central Standard Time" reached the
// formatter and the whole screen failed), and the lint rule keeps `timeZone:` inside lib/dates.
import { ESLint } from 'eslint'
import path from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  browserZone, dateTimeFormat, formatDate, formatInstant, formatRange, formatTime, isValidZone, resolveDatePrefs,
  safeZone, setUserZone, startOfDay, toISODate, zoneAbbr, zoneParts,
} from './dates'

const WINDOWS = ['Central Standard Time', 'Eastern Standard Time', 'Pacific Standard Time', 'W. Europe Standard Time', 'India Standard Time']
const at = new Date('2026-10-15T15:00:00Z')

describe('safe zones', () => {
  afterEach(() => setUserZone(null))

  it('refuses Windows names and accepts IANA ones', () => {
    for (const w of WINDOWS) expect(isValidZone(w)).toBe(false)
    expect(isValidZone('America/Chicago')).toBe(true)
    expect(isValidZone(null)).toBe(false)
    expect(isValidZone('')).toBe(false)
  })

  it('falls back to the user zone, then the browser zone, and logs the bad name once', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(safeZone('America/Chicago')).toBe('America/Chicago')
    expect(safeZone('Central Standard Time')).toBe(browserZone())
    setUserZone('Asia/Tokyo')
    expect(safeZone('Central Standard Time')).toBe('Asia/Tokyo')
    expect(safeZone(null)).toBe('Asia/Tokyo')
    setUserZone('Pacific Standard Time') // a bad setting is ignored
    expect(safeZone(undefined)).toBe(browserZone())
    expect(warn.mock.calls.filter((c) => String(c[0]).includes('Central Standard Time'))).toHaveLength(1)
    warn.mockRestore()
  })

  it('formats in a refused zone without throwing', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    setUserZone('Asia/Tokyo')
    for (const w of WINDOWS) {
      expect(() => zoneParts(at, w)).not.toThrow()
      expect(toISODate(at, w)).toBe('2026-10-16') // read in Tokyo
      expect(formatTime(at, { zone: w, hour12: false })).toBe('00:00')
      expect(() => formatDate(at, 'day', w)).not.toThrow()
      expect(() => formatRange(at, new Date(at.getTime() + 3600_000), false, { zone: w, hour12: true })).not.toThrow()
      expect(() => startOfDay('2026-10-15', w)).not.toThrow()
      expect(zoneAbbr(at, w)).toBe(w)
      expect(Number(formatInstant(at, 'en-US', { hour: 'numeric', hourCycle: 'h23' }, w))).toBe(0)
    }
    expect(Number(formatInstant(at, 'en-US', { hour: 'numeric', hourCycle: 'h23' }, 'America/Chicago'))).toBe(10)
    expect(dateTimeFormat('en-US', {}, 'Central Standard Time').resolvedOptions().timeZone).toBe('Asia/Tokyo')
    expect(dateTimeFormat('en-US', {}).resolvedOptions().timeZone).toBe(browserZone())
    vi.restoreAllMocks()
  })

  it('a stored Windows setting gives the browser zone in the prefs', () => {
    expect(resolveDatePrefs({ timezone: 'Central Standard Time' }).zone).toBe(browserZone())
  })
})

describe('the timeZone lint rule', () => {
  const root = path.resolve(__dirname, '../..')
  const lint = async (file: string, code: string) => {
    const eslint = new ESLint({ cwd: root })
    const [r] = await eslint.lintText(code, { filePath: path.join(root, file) })
    return r.messages.filter((m) => m.ruleId === 'no-restricted-syntax')
  }
  const bad = "export const f = (d: Date, z: string) => new Intl.DateTimeFormat('en-US', { timeZone: z }).format(d)\n"

  it('fails on a timeZone option outside lib/dates', async () => {
    expect(await lint('src/features/calendar/x.ts', bad)).toHaveLength(1)
    expect(await lint('src/features/calendar/x.ts', "export const o = { 'timeZone': 'UTC' }\n")).toHaveLength(1)
    expect(await lint('src/features/calendar/x.tsx', "export const s = (d: Date, timeZone: string) => d.toLocaleString('en', { timeZone })\n")).toHaveLength(1)
  })

  it('allows lib/dates and the helpers', async () => {
    expect(await lint('src/lib/dates.ts', bad)).toHaveLength(0)
    expect(await lint('src/features/calendar/x.ts',
      "import { formatInstant } from '../../lib/dates'\nexport const f = (d: Date, z: string) => formatInstant(d, 'en-US', { weekday: 'short' }, z)\n")).toHaveLength(0)
  })
}, 60_000)

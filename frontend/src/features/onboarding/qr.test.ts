import { describe, expect, it } from 'vitest'
import { encodeQr, penalty, qrPath, versionFor } from './qr'

/** FNV-1a over the rows ("0"/"1", one line per row). */
const hash = (m: boolean[][]) => {
  let h = 0x811c9dc5
  for (const ch of m.map((r) => r.map((x) => (x ? '1' : '0')).join('')).join('\n')) {
    h ^= ch.charCodeAt(0)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16)
}

describe('QR codes', () => {
  it('picks the smallest version that holds the text', () => {
    expect(versionFor(14, 'M')).toBe(1)
    expect(versionFor(15, 'M')).toBe(2)
    expect(versionFor(34, 'M')).toBe(3)
    expect(versionFor(3000, 'M')).toBe(0)
  })
  it('draws the finder patterns, timing and the dark module', () => {
    const q = encodeQr('https://mail.example.com/get-app')
    expect(q.version).toBe(3)
    expect(q.size).toBe(29)
    const m = q.modules
    // Finder corners: a dark ring, a light ring, a dark 3x3 centre.
    for (const [x, y] of [[0, 0], [q.size - 7, 0], [0, q.size - 7]]) {
      expect(m[y][x] && m[y + 6][x + 6] && m[y + 3][x + 3]).toBe(true)
      expect(m[y + 1][x + 1]).toBe(false)
    }
    for (let i = 8; i < q.size - 8; i++) expect(m[6][i]).toBe(i % 2 === 0)
    expect(m[q.size - 8][8]).toBe(true)
  })
  it('matches the code a QR reader decoded when the encoder was written', () => {
    // zbarimg read this matrix as https://mail.example.com/get-app (4 Oct 2026).
    const q = encodeQr('https://mail.example.com/get-app')
    expect(q.mask).toBe(3)
    expect(hash(q.modules)).toBe('8efa7385')
  })
  it('chooses the mask with the lowest penalty', () => {
    const best = encodeQr('https://mail.example.com/get-app')
    for (let mask = 0; mask < 8; mask++) {
      expect(penalty(best.modules)).toBeLessThanOrEqual(penalty(encodeQr('https://mail.example.com/get-app', 'M', mask).modules))
    }
  })
  it('handles long links (version info) and non-ASCII text', () => {
    expect(encodeQr('https://x.org/' + 'a'.repeat(300)).version).toBeGreaterThanOrEqual(7)
    expect(encodeQr('Ünïcødé ✓').size).toBeGreaterThan(20)
    expect(qrPath(encodeQr('x'))).toMatch(/^M\d+,\d+h1v1h-1z/)
  })
})

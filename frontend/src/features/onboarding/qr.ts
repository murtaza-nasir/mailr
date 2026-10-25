// A small QR code encoder (ISO/IEC 18004, byte mode, error correction M or L) for the setup
// guide's "Get the app" code. No dependency: the code only ever holds a short link. The algorithm
// follows the standard: data codewords, Reed–Solomon error correction in interleaved blocks,
// function patterns, the zig-zag placement, and the mask with the lowest penalty.

import { t } from '../../i18n'

export type Ecc = 'L' | 'M'

// Index 0 is unused (versions start at 1).
const ECC_PER_BLOCK: Record<Ecc, number[]> = {
  L: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  M: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
}
const NUM_BLOCKS: Record<Ecc, number[]> = {
  L: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  M: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
}
const FORMAT_ECC: Record<Ecc, number> = { L: 1, M: 0 }

export interface QrMatrix {
  version: number
  size: number
  mask: number
  /** modules[y][x]: true is dark. */
  modules: boolean[][]
}

function rawDataModules(ver: number): number {
  let r = (16 * ver + 128) * ver + 64
  if (ver >= 2) {
    const n = Math.floor(ver / 7) + 2
    r -= (25 * n - 10) * n - 55
    if (ver >= 7) r -= 36
  }
  return r
}

function dataCodewords(ver: number, ecc: Ecc): number {
  return Math.floor(rawDataModules(ver) / 8) - ECC_PER_BLOCK[ecc][ver] * NUM_BLOCKS[ecc][ver]
}

function gfMul(x: number, y: number): number {
  let z = 0
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d)
    z ^= ((y >>> i) & 1) * x
  }
  return z & 0xff
}

function rsDivisor(degree: number): number[] {
  const r = new Array<number>(degree).fill(0)
  r[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < r.length; j++) {
      r[j] = gfMul(r[j], root)
      if (j + 1 < r.length) r[j] ^= r[j + 1]
    }
    root = gfMul(root, 0x02)
  }
  return r
}

function rsRemainder(data: number[], divisor: number[]): number[] {
  const r = new Array<number>(divisor.length).fill(0)
  for (const b of data) {
    const factor = b ^ (r.shift() as number)
    r.push(0)
    divisor.forEach((c, i) => {
      r[i] ^= gfMul(c, factor)
    })
  }
  return r
}

function utf8(text: string): number[] {
  return Array.from(new TextEncoder().encode(text))
}

/** The smallest version (1-40) that holds `n` bytes in byte mode, or 0. */
export function versionFor(n: number, ecc: Ecc): number {
  for (let v = 1; v <= 40; v++) {
    const countBits = v <= 9 ? 8 : 16
    if (4 + countBits + n * 8 <= dataCodewords(v, ecc) * 8) return v
  }
  return 0
}

function codewords(bytes: number[], ver: number, ecc: Ecc): number[] {
  const bits: number[] = []
  const put = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1)
  }
  put(0b0100, 4)
  put(bytes.length, ver <= 9 ? 8 : 16)
  for (const b of bytes) put(b, 8)
  const cap = dataCodewords(ver, ecc) * 8
  put(0, Math.min(4, cap - bits.length))
  put(0, (8 - (bits.length % 8)) % 8)
  for (let pad = 0xec; bits.length < cap; pad ^= 0xec ^ 0x11) put(pad, 8)
  const data: number[] = []
  for (let i = 0; i < bits.length; i += 8) data.push(parseInt(bits.slice(i, i + 8).join(''), 2))
  // Error correction per block, then interleave.
  const numBlocks = NUM_BLOCKS[ecc][ver]
  const eccLen = ECC_PER_BLOCK[ecc][ver]
  const raw = Math.floor(rawDataModules(ver) / 8)
  const numShort = numBlocks - (raw % numBlocks)
  const shortLen = Math.floor(raw / numBlocks)
  const div = rsDivisor(eccLen)
  const blocks: number[][] = []
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortLen - eccLen + (i < numShort ? 0 : 1))
    k += dat.length
    const e = rsRemainder(dat, div)
    if (i < numShort) dat.push(0)
    blocks.push(dat.concat(e))
  }
  const out: number[] = []
  for (let i = 0; i < blocks[0].length; i++) {
    blocks.forEach((b, j) => {
      if (i !== shortLen - eccLen || j >= numShort) out.push(b[i])
    })
  }
  return out
}

function alignmentPositions(ver: number): number[] {
  if (ver === 1) return []
  const n = Math.floor(ver / 7) + 2
  const size = ver * 4 + 17
  const step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (n * 2 - 2)) * 2
  const r = [6]
  for (let pos = size - 7; r.length < n; pos -= step) r.splice(1, 0, pos)
  return r
}

class Grid {
  size: number
  m: boolean[][]
  fn: boolean[][]
  constructor(size: number) {
    this.size = size
    this.m = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
    this.fn = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  }
  set(x: number, y: number, dark: boolean) {
    this.m[y][x] = dark
    this.fn[y][x] = true
  }
}

function drawFormat(g: Grid, ecc: Ecc, mask: number) {
  const data = (FORMAT_ECC[ecc] << 3) | mask
  let rem = data
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
  const bits = ((data << 10) | rem) ^ 0x5412
  const bit = (i: number) => ((bits >>> i) & 1) !== 0
  for (let i = 0; i <= 5; i++) g.set(8, i, bit(i))
  g.set(8, 7, bit(6))
  g.set(8, 8, bit(7))
  g.set(7, 8, bit(8))
  for (let i = 9; i < 15; i++) g.set(14 - i, 8, bit(i))
  for (let i = 0; i < 8; i++) g.set(g.size - 1 - i, 8, bit(i))
  for (let i = 8; i < 15; i++) g.set(8, g.size - 15 + i, bit(i))
  g.set(8, g.size - 8, true)
}

function drawFunctionPatterns(g: Grid, ver: number, ecc: Ecc) {
  const size = g.size
  for (let i = 0; i < size; i++) {
    g.set(6, i, i % 2 === 0)
    g.set(i, 6, i % 2 === 0)
  }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const d = Math.max(Math.abs(dx), Math.abs(dy))
        const x = cx + dx
        const y = cy + dy
        if (x >= 0 && x < size && y >= 0 && y < size) g.set(x, y, d !== 2 && d !== 4)
      }
    }
  }
  const pos = alignmentPositions(ver)
  const n = pos.length
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) g.set(pos[i] + dx, pos[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
      }
    }
  }
  drawFormat(g, ecc, 0) // reserves the format areas; redrawn with the chosen mask
  if (ver >= 7) {
    let rem = ver
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
    const bits = (ver << 12) | rem
    for (let i = 0; i < 18; i++) {
      const b = ((bits >>> i) & 1) !== 0
      const a = size - 11 + (i % 3)
      const c = Math.floor(i / 3)
      g.set(a, c, b)
      g.set(c, a, b)
    }
  }
}

function drawCodewords(g: Grid, data: number[]) {
  const size = g.size
  let i = 0
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j
        const upward = ((right + 1) & 2) === 0
        const y = upward ? size - 1 - vert : vert
        if (!g.fn[y][x] && i < data.length * 8) {
          g.m[y][x] = ((data[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0
          i++
        }
      }
    }
  }
}

function maskHit(mask: number, x: number, y: number): boolean {
  switch (mask) {
    case 0: return (x + y) % 2 === 0
    case 1: return y % 2 === 0
    case 2: return x % 3 === 0
    case 3: return (x + y) % 3 === 0
    case 4: return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0
    case 5: return ((x * y) % 2) + ((x * y) % 3) === 0
    case 6: return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0
    default: return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0
  }
}

function applyMask(g: Grid, mask: number) {
  for (let y = 0; y < g.size; y++) {
    for (let x = 0; x < g.size; x++) if (!g.fn[y][x] && maskHit(mask, x, y)) g.m[y][x] = !g.m[y][x]
  }
}

/** The standard's penalty (rules N1–N4); lower reads better. */
export function penalty(m: boolean[][]): number {
  const size = m.length
  let score = 0
  const lines: boolean[][] = []
  for (let y = 0; y < size; y++) lines.push(m[y])
  for (let x = 0; x < size; x++) lines.push(m.map((row) => row[x]))
  const finder = [true, false, true, true, true, false, true]
  for (const line of lines) {
    let run = 1
    for (let i = 1; i <= size; i++) {
      if (i < size && line[i] === line[i - 1]) run++
      else {
        if (run >= 5) score += 3 + (run - 5)
        run = 1
      }
    }
    // A finder-like 1:1:3:1:1 with four light modules on one side (the edge counts as light).
    for (let i = 0; i + 7 <= size; i++) {
      if (!finder.every((v, k) => line[i + k] === v)) continue
      const before = [1, 2, 3, 4].every((k) => i - k < 0 || !line[i - k])
      const after = [0, 1, 2, 3].every((k) => i + 7 + k >= size || !line[i + 7 + k])
      if (before || after) score += 40
    }
  }
  for (let y = 0; y + 1 < size; y++) {
    for (let x = 0; x + 1 < size; x++) {
      const c = m[y][x]
      if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) score += 3
    }
  }
  const dark = m.reduce((n, row) => n + row.filter(Boolean).length, 0)
  const total = size * size
  const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1
  score += Math.max(0, k) * 10
  return score
}

/** Encode `text` (UTF-8 bytes). Throws when it does not fit a QR code. */
export function encodeQr(text: string, ecc: Ecc = 'M', forceMask?: number): QrMatrix {
  const bytes = utf8(text)
  const version = versionFor(bytes.length, ecc)
  if (!version) throw new Error(t('onboarding.qr.tooLongForAQr'))
  const data = codewords(bytes, version, ecc)
  const size = version * 4 + 17
  let best: Grid | null = null
  let bestMask = 0
  let bestScore = Infinity
  for (let mask = 0; mask < 8; mask++) {
    if (forceMask !== undefined && mask !== forceMask) continue
    const g = new Grid(size)
    drawFunctionPatterns(g, version, ecc)
    drawCodewords(g, data)
    applyMask(g, mask)
    drawFormat(g, ecc, mask)
    const s = penalty(g.m)
    if (s < bestScore) {
      best = g
      bestMask = mask
      bestScore = s
    }
  }
  return { version, size, mask: bestMask, modules: best!.m }
}

/** One SVG path ("M x y h1 v1 h-1 z" per dark module) for a matrix, with a quiet zone of `border`. */
export function qrPath(q: QrMatrix, border = 4): string {
  const parts: string[] = []
  for (let y = 0; y < q.size; y++) {
    for (let x = 0; x < q.size; x++) if (q.modules[y][x]) parts.push(`M${x + border},${y + border}h1v1h-1z`)
  }
  return parts.join('')
}

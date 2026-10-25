// The clock and online hooks live once, in hooks/; no app keeps its own copy.
import { describe, expect, it } from 'vitest'

const SRC = import.meta.glob(['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!./*.ts'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>

describe('shared hooks', () => {
  it('no copy of useNow or useOnline outside hooks/', () => {
    const copies = Object.entries(SRC).filter(([, t]) => /function use(Now|Online)\(/.test(t)).map(([p]) => p)
    expect(copies).toEqual([])
  })
})

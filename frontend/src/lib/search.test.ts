import { describe, expect, it } from 'vitest'
import { buildQuery, EMPTY_SEARCH, hasToken, parseQuery, setOperator, toggleToken, tokenize } from './search'

describe('buildQuery', () => {
  it('builds operators', () => {
    expect(buildQuery({ ...EMPTY_SEARCH, from: 'alice@x.example', subject: 'q3 plan', hasAttachment: true }))
      .toBe('from:alice@x.example subject:"q3 plan" has:attachment')
  })
  it('handles size, exclusions, and folders', () => {
    expect(buildQuery({ ...EMPTY_SEARCH, size: '5', sizeUnit: 'MB', doesntHave: 'foo bar', searchIn: 'inbox' }))
      .toBe('-{foo bar} larger:5M in:inbox')
    expect(buildQuery({ ...EMPTY_SEARCH, searchIn: 'label:Job Applications' })).toBe('label:"Job Applications"')
  })
  it('date within relative to today', () => {
    expect(buildQuery({ ...EMPTY_SEARCH, dateWithin: '1w' })).toBe('newer_than:7d')
  })
  it('date within an anchor', () => {
    expect(buildQuery({ ...EMPTY_SEARCH, dateWithin: '1d', dateAnchor: '2026-09-10' })).toBe('after:2026/09/09 before:2026/09/12')
  })
})

describe('parseQuery', () => {
  it('round-trips the common fields', () => {
    const f = parseQuery('from:alice subject:"q3 plan" budget -{foo bar} larger:5M in:inbox has:attachment newer_than:7d')
    expect(f.from).toBe('alice')
    expect(f.subject).toBe('q3 plan')
    expect(f.hasWords).toBe('budget')
    expect(f.doesntHave).toBe('foo bar')
    expect(f.size).toBe('5')
    expect(f.sizeUnit).toBe('MB')
    expect(f.searchIn).toBe('inbox')
    expect(f.hasAttachment).toBe(true)
    expect(f.dateWithin).toBe('1w')
  })
})

describe('chips', () => {
  it('toggles tokens', () => {
    expect(toggleToken('budget', 'has:attachment')).toBe('budget has:attachment')
    expect(toggleToken('budget has:attachment', 'has:attachment')).toBe('budget')
    expect(hasToken('a is:unread', 'is:unread')).toBe(true)
  })
  it('sets operators', () => {
    expect(setOperator('from:bob report', 'from', 'alice smith')).toBe('report from:"alice smith"')
    expect(setOperator('from:bob report', 'from', '')).toBe('report')
  })
  it('tokenizes quoted values', () => {
    expect(tokenize('subject:"a b" "exact phrase" -{x y}')).toEqual(['subject:"a b"', '"exact phrase"', '-{x y}'])
  })
})

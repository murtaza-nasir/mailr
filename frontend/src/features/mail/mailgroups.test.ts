import { describe, expect, it } from 'vitest'
import {
  actionVerb,
  canUnsubscribe,
  dateSpan,
  DEFAULT_SCOPE,
  groupLabel,
  groupStats,
  percent,
  previewLines,
  protectedText,
  scopeQuery,
  subscriptionAddress,
  subscriptionName,
  unsubscribeLabel,
  unsubscribeText,
} from './mailgroups'

describe('subscriptions wording', () => {
  it('names the unsubscribe method', () => {
    expect(unsubscribeLabel('one_click')).toBe('One-click')
    expect(unsubscribeLabel('mailto')).toBe('By email')
    expect(unsubscribeLabel('link')).toBe('Web page')
    expect(unsubscribeLabel('unknown')).toBe('Checking')
    expect(unsubscribeLabel(null)).toBe('No link')
    expect(canUnsubscribe('none')).toBe(false)
    expect(canUnsubscribe(null)).toBe(false)
    expect(canUnsubscribe('mailto')).toBe(true)
  })

  it('rounds and clamps the read share', () => {
    expect(percent(1 / 3)).toBe('33%')
    expect(percent(1.2)).toBe('100%')
    expect(percent(-1)).toBe('0%')
  })

  it('names a subscription by its sender, then its list', () => {
    expect(subscriptionName({ name: 'Weekly', list_id: 'w.example', email: 'a@w.example' })).toBe('Weekly')
    expect(subscriptionName({ name: '', list_id: 'w.example', email: 'a@w.example' })).toBe('w.example')
    expect(subscriptionAddress({ kind: 'list', list_id: 'w.example', email: 'a@w.example' })).toBe('a@w.example · list w.example')
    expect(subscriptionAddress({ kind: 'sender', list_id: null, email: 'a@w.example' })).toBe('a@w.example')
  })

  it('says what an unsubscribe does before it runs', () => {
    expect(unsubscribeText({ key: 'k', method: 'one_click', url: 'https://news.example/u/1?x=2' })).toContain('news.example')
    expect(unsubscribeText({ key: 'k', method: 'mailto', to: 'leave@news.example' })).toContain('leave@news.example')
    expect(unsubscribeText({ key: 'k', method: 'link', url: 'https://x.example' })).toContain('web page')
    expect(unsubscribeText({ key: 'k', method: 'none' })).toContain('No way')
  })
})

describe('cleanup scope and preview', () => {
  it('sends only what differs from the default scope', () => {
    expect(scopeQuery(DEFAULT_SCOPE)).toEqual({})
    expect(scopeQuery({ ...DEFAULT_SCOPE, older_than_days: 365, include_starred: true })).toEqual({
      older_than_days: 365,
      include_starred: true,
    })
  })

  it('labels groups', () => {
    expect(groupLabel('category', { key: '', label: '' })).toBe('Uncategorised')
    expect(groupLabel('category', { key: 'promotions', label: 'Promotions' })).toBe('Promotions')
    expect(groupLabel('sender', { key: 'a@b.example', label: 'a@b.example' })).toBe('a@b.example')
  })

  it('lists what is left alone', () => {
    expect(protectedText({ starred: 0, important: 0, replied: 0 })).toBe('')
    expect(protectedText({ starred: 3, important: 0, replied: 0 })).toBe('3 starred')
    expect(protectedText({ starred: 3, important: 2, replied: 5 })).toBe('3 starred, 2 important and 5 with your replies')
  })

  it('says exactly what an action changes, with Undo and background notes', () => {
    const p = { conversations: 120, messages: 340, protected: { starred: 1, important: 0, replied: 2 }, background: false, undo: true }
    const lines = previewLines(p, 'trash')
    expect(lines[0]).toBe('120 conversations (340 messages) move to Trash. Nothing is deleted forever.')
    expect(lines[1]).toBe('Left alone: 1 starred and 2 with your replies.')
    expect(lines.at(-1)).toBe('You can undo it afterwards.')
    const big = previewLines({ ...p, conversations: 12000, background: true, undo: false }, 'archive')
    expect(big).toContain('You can keep working while the change is made.')
    expect(big.at(-1)).toContain("can't be undone")
    expect(previewLines({ ...p, conversations: 0 }, 'read')[0]).toBe('Nothing to change. Left alone: 1 starred and 2 with your replies.')
    expect(previewLines(p, 'category', 'updates')[0]).toContain('move to Updates')
  })

  it('names the confirm button', () => {
    expect(actionVerb('trash')).toBe('Move to Trash')
    expect(actionVerb('label', 'Receipts')).toBe('Label “Receipts”')
    expect(actionVerb('category', 'promotions')).toBe('Move to Promotions')
  })

  it('summarises a group', () => {
    expect(groupStats({ conversations: 1, messages: 2, size: 2048, unread: 0 })).toBe('1 conversation · 2 messages · 2 KB')
    expect(groupStats({ conversations: 3, messages: 3, size: 0, unread: 2 })).toContain('2 unread')
    expect(dateSpan(null, null)).toBe('')
    expect(dateSpan('2020-01-05T00:00:00Z', '2020-01-20T00:00:00Z')).toBe('Jan 2020')
    expect(dateSpan('2019-03-05T00:00:00Z', '2024-06-20T00:00:00Z')).toBe('Mar 2019 – Jun 2024')
  })
})

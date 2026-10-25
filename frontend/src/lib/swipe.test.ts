import { describe, expect, it } from 'vitest'
import { logicalSwipe, readSwipes, swipeEffect, writeSwipes, DEFAULT_SWIPES } from './swipe'

const unread = { unread_count: 2, starred: false }
const read = { unread_count: 0, starred: true }

describe('swipe effects', () => {
  it('archive and delete in ordinary lists', () => {
    expect(swipeEffect('archive', unread, 'inbox')).toMatchObject({ action: 'archive', removes: true })
    expect(swipeEffect('archive', unread, 'all')).toMatchObject({ action: 'archive', removes: false })
    expect(swipeEffect('trash', unread, 'inbox')).toMatchObject({ action: 'trash', tone: 'danger', removes: true })
  })
  it('Trash and Spam turn archive into restore and not spam; delete is no swipe in Trash', () => {
    expect(swipeEffect('archive', unread, 'trash').action).toBe('untrash')
    expect(swipeEffect('archive', unread, 'spam').action).toBe('not_spam')
    expect(swipeEffect('trash', unread, 'trash').action).toBeNull()
  })
  it('read and star toggle with the row', () => {
    expect(swipeEffect('read', unread, 'inbox').action).toBe('read')
    expect(swipeEffect('read', read, 'inbox').action).toBe('unread')
    expect(swipeEffect('star', unread, 'inbox').action).toBe('star')
    expect(swipeEffect('star', read, 'starred')).toMatchObject({ action: 'unstar', removes: true })
  })
})

describe('swipe settings', () => {
  it('default to Archive right and Delete left, and keep a choice', () => {
    expect(readSwipes(null)).toEqual(DEFAULT_SWIPES)
    const m = new Map<string, string>()
    const store = { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
    writeSwipes({ right: 'read', left: 'snooze' }, store)
    expect(readSwipes(store)).toEqual({ right: 'read', left: 'snooze' })
    m.set('mailr.swipe', '{"right":"bogus"}')
    expect(readSwipes(store)).toEqual(DEFAULT_SWIPES)
  })
})

describe('logicalSwipe', () => {
  it('a swipe toward the end uses the same setting in both directions', () => {
    expect(logicalSwipe('right', false)).toBe('right')
    expect(logicalSwipe('left', false)).toBe('left')
    expect(logicalSwipe('left', true)).toBe('right')
    expect(logicalSwipe('right', true)).toBe('left')
  })
})

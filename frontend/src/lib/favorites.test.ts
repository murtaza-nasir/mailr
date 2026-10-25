// ACT-SURFACES: web.nav
// Favorites and conversation drops on the nav: nav.favorite_add,
// nav.favorite_remove, nav.favorite_reorder; the drop plans of mail.label, mail.move,
// mail.archive, mail.delete, mail.spam, mail.star and mail.important.
import { describe, expect, it } from 'vitest'
import type { Label } from '../api/types'
import {
  addFavorite, canFavorite, CLASSIC_FAVORITES_SKIP_SHOWN, DEFAULT_FAVORITES, favoritesForTheme, favoritesToShow, moveFavorite, moveFavoriteBefore, removeFavorite, resolveFavorites,
} from './favorites'
import { dragLabel, dropPlan } from './mailDrop'
import { feedKey, type MatchState } from './shortcuts'

const label = (id: number, name: string, role: Label['role'] = null): Label =>
  ({ id, name, role, color: null, show_in_list: 'show', show_in_message_list: true, sort: 0, home_account_id: null, unread: 0, total: 0 })
const LABELS = [label(1, 'Inbox', 'inbox'), label(10, 'Financial'), label(11, 'Work/Clients')]

describe('favorites', () => {
  it('defaults to Inbox, Sent and Drafts', () => {
    expect(resolveFavorites(undefined, LABELS)).toEqual(DEFAULT_FAVORITES)
  })
  it('drops deleted labels and unknown views silently, keeps label favorites while labels load', () => {
    expect(resolveFavorites(['inbox', 'label:10', 'label:99', 'search', 'category:social', 'label:1'], LABELS)).toEqual(['inbox', 'label:10', 'category:social'])
    expect(resolveFavorites(['label:10', 'sent'], [], false)).toEqual(['sent'])
  })
  it('nav.favorite_add and nav.favorite_remove', () => {
    expect(addFavorite(['inbox'], 'label:10')).toEqual(['inbox', 'label:10'])
    expect(addFavorite(['inbox'], 'inbox')).toEqual(['inbox'])
    expect(removeFavorite(['inbox', 'sent'], 'inbox')).toEqual(['sent'])
    expect(canFavorite('trash')).toBe(true)
    expect(canFavorite('search')).toBe(false)
    expect(canFavorite('attachments')).toBe(false)
  })
  it('nav.favorite_reorder by step and by drag', () => {
    const l = ['inbox', 'sent', 'drafts']
    expect(moveFavorite(l, 'sent', -1)).toEqual(['sent', 'inbox', 'drafts'])
    expect(moveFavorite(l, 'drafts', 1)).toBe(l)
    expect(moveFavoriteBefore(l, 'drafts', 'inbox')).toEqual(['drafts', 'inbox', 'sent'])
    expect(moveFavoriteBefore(l, 'inbox', null)).toEqual(['sent', 'drafts', 'inbox'])
    expect(moveFavoriteBefore(l, 'inbox', 'inbox')).toBe(l)
  })
  it('nav.favorite_reorder steps past the favorites a nav leaves out', () => {
    const l = ['inbox', 'label:10', 'sent', 'label:11']
    const shown = ['label:10', 'label:11']
    expect(moveFavorite(l, 'label:11', -1, shown)).toEqual(['inbox', 'label:11', 'sent', 'label:10'])
    expect(moveFavorite(l, 'label:10', -1, shown)).toBe(l)
    expect(moveFavorite(l, 'label:11', 1, shown)).toBe(l)
  })
  it('Classic leaves the folders it always shows out of Favorites (option A)', () => {
    expect(CLASSIC_FAVORITES_SKIP_SHOWN).toBe(true)
    const e = (view: string) => ({ view })
    const shown = new Set(['inbox', 'starred', 'snoozed', 'sent', 'drafts'])
    // Option A: only what the user added; the default list leaves nothing, so the group hides.
    expect(favoritesToShow(['inbox', 'label:10', 'sent'].map(e), shown, true).map((x) => x.view)).toEqual(['label:10'])
    expect(favoritesToShow(DEFAULT_FAVORITES.map(e), shown, true)).toEqual([])
    // Option B (the revert): the whole list once it holds one entry of its own.
    expect(favoritesToShow(['inbox', 'label:10', 'sent'].map(e), shown, false).map((x) => x.view)).toEqual(['inbox', 'label:10', 'sent'])
    expect(favoritesToShow(DEFAULT_FAVORITES.map(e), shown, false)).toEqual([])
  })
  it('g 1 … g 9 follow the favorites the theme draws', () => {
    const keys = ['inbox', 'label:10', 'sent', 'trash']
    expect(favoritesForTheme(keys, 'classic')).toEqual(['label:10', 'trash'])
    expect(favoritesForTheme(keys, 'office')).toEqual(keys)
  })
  it('g 1 … g 9 go to a favorite', () => {
    let state: MatchState = { pending: [] }
    state = feedKey(state, { key: 'g' }).state
    const r = feedKey(state, { key: '3' })
    expect(r.action).toBe('goFavorite')
    expect(r.arg).toBe('3')
  })
})

describe('dropPlan', () => {
  const ctx = (from: string, shift = false) => ({ from, shift, labels: LABELS })
  it('mail.label: a label adds itself and the mail stays', () => {
    expect(dropPlan('label:10', ctx('inbox'))).toEqual({ verb: 'Label: Financial', req: { action: 'add_labels', label_ids: [10] }, removes: false })
    expect(dropPlan('label:11', ctx('inbox'))?.verb).toBe('Label: Clients')
  })
  it('mail.move: Shift moves to the label, out of the label list it came from', () => {
    expect(dropPlan('label:10', ctx('inbox', true))).toEqual({ verb: 'Move to Financial', req: { action: 'move', label_id: 10 }, removes: true })
    expect(dropPlan('label:10', ctx('label:11', true))?.req).toEqual({ action: 'move', label_id: 10, from_label_id: 11 })
  })
  it('folders move with the toolbar requests', () => {
    expect(dropPlan('trash', ctx('inbox'))).toEqual({ verb: 'Move to Trash', req: { action: 'trash' }, removes: true }) // mail.delete
    expect(dropPlan('spam', ctx('inbox'))?.req).toEqual({ action: 'spam' }) // mail.spam
    expect(dropPlan('all', { ...ctx('inbox'), contextLabelId: 10 })?.req).toEqual({ action: 'archive', from_label_id: 10 }) // mail.archive
    expect(dropPlan('inbox', ctx('trash'))?.req).toEqual({ action: 'untrash' })
    expect(dropPlan('inbox', ctx('spam'))?.req).toEqual({ action: 'not_spam' })
    expect(dropPlan('inbox', ctx('all'))?.req).toEqual({ action: 'inbox' })
    expect(dropPlan('starred', ctx('inbox'))?.req).toEqual({ action: 'star' }) // mail.star
    expect(dropPlan('important', ctx('inbox'))?.req).toEqual({ action: 'important' }) // mail.important
  })
  it('no drop on the view itself, on places that are not folders, or Archive from Trash', () => {
    expect(dropPlan('inbox', ctx('inbox'))).toBeNull()
    expect(dropPlan('label:10', ctx('label:10', true))).toBeNull()
    expect(dropPlan('sent', ctx('inbox'))).toBeNull()
    expect(dropPlan('drafts', ctx('inbox'))).toBeNull()
    expect(dropPlan('snoozed', ctx('inbox'))).toBeNull()
    expect(dropPlan('category:social', ctx('inbox'))).toBeNull()
    expect(dropPlan('all', ctx('trash'))).toBeNull()
    expect(dropPlan('label:1', ctx('all'))).toBeNull() // a role label is not a user label
  })
  it('drag image text', () => {
    expect(dragLabel(['Quarterly report'], 1)).toBe('Quarterly report')
    expect(dragLabel([''], 1)).toBe('(no subject)')
    expect(dragLabel(['x'], 3)).toBe('3 conversations')
  })
})

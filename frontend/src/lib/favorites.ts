// Favorites in the mail navigation: an ordered list of view keys kept in the
// user's settings (`nav_favorites`), so every theme and every device shows the same list.

import type { Label } from '../api/types'
import { CATEGORIES, isSystemView } from './views'

export const DEFAULT_FAVORITES = ['inbox', 'sent', 'drafts']

/**
 * Classic's Favorites leaves out the folders Classic always shows right below it (Inbox, Starred,
 * Snoozed, Sent, Drafts), so the group holds only what the user added there (option A, decision
 * of 4 Oct 2026). `false` brings back option B: the group shows the whole list in the user's own
 * order, repeats included, and hides only while it would hold nothing but repeats.
 */
export const CLASSIC_FAVORITES_SKIP_SHOWN = true

/** The favoritable folders Classic's sidebar always shows (its first block, under Favorites). */
export const CLASSIC_SHOWN_FOLDERS: readonly string[] = ['inbox', 'starred', 'snoozed', 'sent', 'drafts']

/** The favorites in the order a theme draws them, for g 1 … g 9. */
export function favoritesForTheme(keys: string[], theme: string): string[] {
  if (theme !== 'classic' || !CLASSIC_FAVORITES_SKIP_SHOWN) return keys
  const shown = new Set(CLASSIC_SHOWN_FOLDERS)
  return favoritesToShow(keys.map((view) => ({ view })), shown, true).map((e) => e.view)
}

/**
 * The favorites a nav draws, given the views it already shows elsewhere. With `skipShown`, those
 * views drop out; without it, the whole list shows unless every entry is a repeat.
 */
export function favoritesToShow<T extends { view: string }>(entries: T[], shown: ReadonlySet<string>, skipShown: boolean): T[] {
  const extra = entries.filter((e) => !shown.has(e.view))
  if (skipShown) return extra
  return extra.length ? entries : []
}

/** Views a favorite may name. Search and the Attachments view are not places, so they are left out. */
export function canFavorite(view: string): boolean {
  if (view.startsWith('label:')) return /^label:\d+$/.test(view)
  if (view.startsWith('category:')) return CATEGORIES.some((c) => `category:${c.id}` === view)
  return isSystemView(view)
}

/** The stored list as the nav shows it: known views only; deleted labels drop out silently. */
export function resolveFavorites(keys: string[] | undefined, labels: Label[], labelsLoaded = true): string[] {
  const list = keys ?? DEFAULT_FAVORITES
  const ids = new Set(labels.filter((l) => !l.role).map((l) => l.id))
  return [...new Set(list)].filter((k) => {
    if (!canFavorite(k)) return false
    // While labels load, label favorites wait rather than flash out.
    if (k.startsWith('label:')) return labelsLoaded && ids.has(Number(k.slice(6)))
    return true
  })
}

export function addFavorite(list: string[], view: string): string[] {
  return list.includes(view) ? list : [...list, view]
}

export function removeFavorite(list: string[], view: string): string[] {
  return list.filter((k) => k !== view)
}

/**
 * One step up (-1) or down (+1); the ends stay put. `among` is the part of the list a nav draws
 * (Classic leaves some favorites out): the step goes past the entries it does not draw, which
 * keep their places.
 */
export function moveFavorite(list: string[], view: string, delta: -1 | 1, among?: readonly string[]): string[] {
  const order = among ? list.filter((k) => among.includes(k)) : list
  const a = order.indexOf(view)
  const other = order[a + delta]
  if (a < 0 || other === undefined) return list
  const i = list.indexOf(view)
  const j = list.indexOf(other)
  const out = [...list]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out
}

/** Drag reorder: `view` goes just before `before` (null: to the end). */
export function moveFavoriteBefore(list: string[], view: string, before: string | null): string[] {
  if (view === before || !list.includes(view)) return list
  const rest = list.filter((k) => k !== view)
  const at = before === null ? rest.length : rest.indexOf(before)
  if (at < 0) return list
  return [...rest.slice(0, at), view, ...rest.slice(at)]
}

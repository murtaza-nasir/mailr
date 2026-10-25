// Favorites: the user's own short list of folders and labels at the top of the
// mail navigation in every theme. The list lives in the settings (nav_favorites), so it is the
// same on every device. Right-click any folder or label to add or remove it; drag a favorite,
// or press Alt+↑/↓ on it, to reorder.
//
// Also the one hook every theme's nav entry uses (useNavTarget): the right-click menu, the drop
// target for dragged conversations, and, for a favorite, its reorder drag and keys.

import type { QueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, BookmarkMinus, BookmarkPlus } from 'lucide-react'
import { useMemo, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import { qk, useLabels, useSettings, useUpdateSettings } from '../../api/hooks'
import type { Label, Me } from '../../api/types'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import {
  addFavorite, canFavorite, DEFAULT_FAVORITES, moveFavorite, moveFavoriteBefore, removeFavorite, resolveFavorites,
} from '../../lib/favorites'
import { labelLeaf } from '../../lib/format'
import { dotStyle } from '../../lib/labels'
import { toast } from '../../stores/ui'
import { useCanDragRows, useDropTarget, useMailDrag, type DropOver } from '../mail/dragDrop'
import { useMailboxes, type NavEntry } from './mailboxes'
import { t } from '../../i18n'

export const FAVORITE_DRAG_TYPE = 'application/x-mailr-favorite'

/** The favorites in order, and the changes the menus and drags make. */
export function useFavorites() {
  const settings = useSettings()
  const upd = useUpdateSettings()
  const labelsQ = useLabels()
  const keys = useMemo(
    () => resolveFavorites(settings.nav_favorites, labelsQ.data ?? [], labelsQ.isSuccess),
    [settings.nav_favorites, labelsQ.data, labelsQ.isSuccess],
  )
  const save = (next: string[], message?: string) => {
    if (next === keys) return
    const prev = keys
    upd.mutate({ nav_favorites: next })
    if (message) toast({ message, actionLabel: t('common.undo'), onAction: () => upd.mutate({ nav_favorites: prev }) })
  }
  return {
    keys,
    isFavorite: (view: string) => keys.includes(view),
    // ACT nav.favorite_add @web.nav
    add: (view: string) => save(addFavorite(keys, view), t('layout.favorites.addedToFavorites')),
    // ACT nav.favorite_remove @web.nav
    remove: (view: string) => save(removeFavorite(keys, view), t('layout.favorites.removedFromFavorites')),
    // ACT nav.favorite_reorder @web.nav
    move: (view: string, delta: -1 | 1, among?: readonly string[]) => save(moveFavorite(keys, view, delta, among)),
    moveBefore: (view: string, before: string | null) => save(moveFavoriteBefore(keys, view, before)),
  }
}

/** The favorites as nav entries, with the same counts the folders and labels show elsewhere. */
export function useFavoriteEntries(): NavEntry[] {
  const { keys } = useFavorites()
  const { labels, primary, secondary, categories } = useMailboxes()
  return useMemo(() => {
    const system = new Map([...primary, ...secondary, ...categories].map((e) => [e.view, e]))
    return keys.map((k) => system.get(k) ?? labelEntry(k, labels)).filter((e): e is NavEntry => !!e)
  }, [keys, labels, primary, secondary, categories])
}

function labelEntry(view: string, labels: Label[]): NavEntry | null {
  const l = view.startsWith('label:') ? labels.find((x) => x.id === Number(view.slice(6))) : undefined
  if (!l) return null
  return {
    view,
    title: labelLeaf(l.name),
    tip: l.name,
    icon: (
      <span className="label-dot" aria-hidden>
        <span style={dotStyle(l.color)} />
      </span>
    ),
    count: l.unread || undefined,
    bold: l.unread > 0,
    unread: l.unread,
    total: l.total,
  }
}

/** The favorites keys at this moment, for key handlers outside React (g 1 … g 9). */
export function currentFavorites(qc: QueryClient): string[] {
  const me = qc.getQueryData<Me>(qk.me)
  const labels = qc.getQueryData<Label[]>(qk.labels)
  return resolveFavorites(me?.settings?.nav_favorites ?? DEFAULT_FAVORITES, Array.isArray(labels) ? labels : [], Array.isArray(labels))
}

/** Add, remove and move items, the same in every theme's menus. */
export function FavoriteMenuItems({ view, close, among }: { view: string; close: () => void; among?: readonly string[] }) {
  const fav = useFavorites()
  if (!canFavorite(view)) return null
  // The order this nav draws: a favorite it leaves out does not count as a step.
  const order = among ? fav.keys.filter((k) => among.includes(k)) : fav.keys
  const i = order.indexOf(view)
  if (fav.keys.indexOf(view) < 0)
    return (
      <MenuItem icon={<BookmarkPlus />} onSelect={() => { close(); fav.add(view) }}>
        {t('layout.favorites.addToFavorites')}
      </MenuItem>
    )
  const removeItem = (
    <MenuItem icon={<BookmarkMinus />} onSelect={() => { close(); fav.remove(view) }}>
      {t('layout.favorites.removeFromFavorites')}
    </MenuItem>
  )
  // A favorite this nav leaves out of its Favorites group has no place there to move.
  if (i < 0) return removeItem
  return (
    <>
      {removeItem}
      <MenuItem icon={<ArrowUp />} disabled={i === 0} shortcut="Alt+↑" onSelect={() => { close(); fav.move(view, -1, among) }}>
        {t('layout.favorites.moveUpInFavorites')}
      </MenuItem>
      <MenuItem icon={<ArrowDown />} disabled={i === order.length - 1} shortcut="Alt+↓" onSelect={() => { close(); fav.move(view, 1, among) }}>
        {t('layout.favorites.moveDownInFavorites')}
      </MenuItem>
    </>
  )
}

const favoriteDrag = { view: null as string | null }

/** Focus a favorite again after it moved (React moves the element, and the move drops focus). */
function refocus(view: string) {
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-favorite="${CSS.escape(view)}"]`)?.focus())
}

interface NavTargetOptions {
  /** The entry is drawn in the Favorites list: it can be dragged and moved with Alt+↑/↓. */
  favorite?: boolean
  /** Where the drop verb shows: beside the entry (side panes) or under it (bars). */
  place?: DropOver['place']
  /** The entry's title, for the menu's name. */
  title?: string
  /** The favorites this nav draws, when it leaves some out (Classic): moves step past the rest. */
  among?: readonly string[]
}

/**
 * Everything a nav entry needs beyond its link, in every theme: `props` to spread on it,
 * `className` to add (drop highlight, reorder line), and `menu`, the right-click menu to render
 * next to it.
 */
export function useNavTarget(view: string, opts: NavTargetOptions = {}) {
  const drop = useDropTarget(view, opts.place)
  const menu = useAnchor()
  const fav = useFavorites()
  const canDrag = useCanDragRows()
  const [line, setLine] = useState<'before' | 'after' | null>(null)
  const mailDrag = useMailDrag((s) => s.drag !== null)
  const favoriteOk = canFavorite(view)

  const props: Record<string, unknown> = { ...drop.props, 'data-drop-view': view }
  if (favoriteOk) {
    // No focus on a right press: a half-hidden entry would scroll into view, and the menu would
    // close on that scroll as it opens.
    props.onMouseDown = (e: MouseEvent) => {
      if (e.button === 2) e.preventDefault()
    }
    props.onContextMenu = (e: MouseEvent) => {
      if (e.shiftKey) return // Shift+right-click keeps the browser's menu
      e.preventDefault()
      const at = new DOMRect(e.clientX, e.clientY, 0, 0)
      // After any scroll already under way (the menu would close on it at once).
      requestAnimationFrame(() => menu.show(at))
    }
  }
  if (opts.favorite) {
    props['data-favorite'] = view
    props.onKeyDown = (e: KeyboardEvent) => {
      if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
      e.preventDefault()
      e.stopPropagation()
      fav.move(view, e.key === 'ArrowUp' ? -1 : 1, opts.among)
      refocus(view)
    }
    if (canDrag) {
      props.draggable = true
      props.onDragStart = (e: DragEvent) => {
        favoriteDrag.view = view
        e.dataTransfer.setData(FAVORITE_DRAG_TYPE, view)
        e.dataTransfer.effectAllowed = 'move'
      }
      props.onDragEnd = () => {
        favoriteDrag.view = null
        setLine(null)
      }
      if (!mailDrag) {
        props.onDragOver = (e: DragEvent<HTMLElement>) => {
          if (!favoriteDrag.view || favoriteDrag.view === view) return
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
          const r = e.currentTarget.getBoundingClientRect()
          setLine(e.clientY > r.top + r.height / 2 ? 'after' : 'before')
        }
        props.onDragLeave = () => setLine(null)
        props.onDrop = (e: DragEvent) => {
          const moving = favoriteDrag.view
          const where = line
          favoriteDrag.view = null
          setLine(null)
          if (!moving || moving === view) return
          e.preventDefault()
          const order = opts.among ? fav.keys.filter((k) => opts.among!.includes(k)) : fav.keys
          const i = order.indexOf(view)
          fav.moveBefore(moving, where === 'after' ? (order[i + 1] ?? null) : view)
        }
      }
    }
  }

  const className = `${drop.over ? ' drop-over' : ''}${line ? ` fav-drop-${line}` : ''}`
  const element: ReactNode = favoriteOk ? (
    <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="right-start" minWidth={240} label={opts.title ? t('layout.favorites.optionsFor', { optsTitle: opts.title }) : t('layout.favorites.options')}>
      <FavoriteMenuItems view={view} close={menu.close} among={opts.among} />
    </Popover>
  ) : null
  return { props, className, menu: element }
}

// Pinned folders in each account's group (lib/accountFolders.ts). With "Show each account separately" on, an
// account's group lists its system folders, the folders pinned to that account in the user's
// order, and one row "More folders (n)" that opens to the rest. The pins, the open "More folders"
// and the "show unread folders" option are settings, so they follow the user to every device.
//
// Pin and unpin from a folder's ⋮ ("Pin to <address>", "Unpin"), or by drag: a folder dropped on
// a pinned folder (or on the "Pin here" line a drag shows) is pinned there, a pinned folder
// dropped on "More folders" or on a folder below it is unpinned. Alt+↑/↓ on a pinned folder moves
// it, as favorites do (features/layout/favorites.tsx).

import { ArrowDown, ArrowUp, Pin, PinOff } from 'lucide-react'
import { useState, type DragEvent, type KeyboardEvent } from 'react'
import { create } from 'zustand'
import { useSettings, useUpdateSettings } from '../../api/hooks'
import { MenuItem } from '../../components/Popover'
import { movePin, pinFolder, pinsOf, unpinFolder, type FolderPins } from '../../lib/accountFolders'
import { toast } from '../../stores/ui'
import { useCanDragRows, useDragging } from '../mail/dragDrop'
import { t } from '../../i18n'

export const FOLDER_DRAG_TYPE = 'application/x-mailr-folder'

/** The folder being dragged in the navigation (not a conversation): which label, from which
 *  account's group, and whether it was pinned there. */
interface FolderDrag { labelId: number; accountId: number | null; pinned: boolean }
export const useFolderDrag = create<{ drag: FolderDrag | null; set: (d: FolderDrag | null) => void }>((set) => ({
  drag: null,
  set: (drag) => set({ drag }),
}))

/** The pins and the open "More folders" of every account, and the changes the menus and drags make. */
export function useAccountPins() {
  const s = useSettings()
  const upd = useUpdateSettings()
  const pins: FolderPins = s.account_folder_pins ?? {}
  const open = s.account_folders_more_open ?? []
  const save = (next: FolderPins, message?: string) => {
    const prev = pins
    upd.mutate({ account_folder_pins: next })
    if (message) toast({ message, actionLabel: t('common.undo'), onAction: () => upd.mutate({ account_folder_pins: prev }) })
  }
  return {
    pinsOf: (accountId: number) => pinsOf(pins, accountId),
    // ACT mail.account_folder_pin @web.nav
    pin: (accountId: number, labelId: number, before: number | null = null, address?: string) =>
      save(pinFolder(pins, accountId, labelId, before), address && !pinsOf(pins, accountId).includes(labelId) ? t('layout.folderPins.pinnedTo', { address }) : undefined),
    // ACT mail.account_folder_unpin @web.nav
    unpin: (accountId: number, labelId: number) => save(unpinFolder(pins, accountId, labelId), t('layout.folderPins.unpinned')),
    // ACT mail.account_folder_reorder @web.nav
    move: (accountId: number, labelId: number, delta: -1 | 1, among?: readonly number[]) => save(movePin(pins, accountId, labelId, delta, among)),
    moreOpen: (accountId: number) => open.includes(accountId),
    // ACT mail.account_folders_more @web.nav
    setMoreOpen: (accountId: number, v: boolean) =>
      upd.mutate({ account_folders_more_open: v ? [...open.filter((x) => x !== accountId), accountId] : open.filter((x) => x !== accountId) }),
    showUnread: !!s.account_folders_show_unread,
  }
}

/** Where a folder sits in an account's group, for its menu, keys and drag. */
export interface PinPlace {
  accountId: number
  address: string
  pinned: boolean
  /** The pinned folders shown, in order (for Move up/down and the drop line). */
  order: readonly number[]
}

/** Pin to <address> / Unpin, Move up / Move down: the items of a folder's ⋮ in an account group. */
export function PinMenuItems({ place, labelId, close }: { place: PinPlace; labelId: number; close: () => void }) {
  const p = useAccountPins()
  if (!place.pinned)
    return (
      <MenuItem icon={<Pin />} onSelect={() => { close(); p.pin(place.accountId, labelId, null, place.address) }}>
        {t('layout.folderPins.pinTo', { address: place.address })}
      </MenuItem>
    )
  const i = place.order.indexOf(labelId)
  return (
    <>
      <MenuItem icon={<PinOff />} onSelect={() => { close(); p.unpin(place.accountId, labelId) }}>
        {t('layout.folderPins.unpin')}
      </MenuItem>
      <MenuItem icon={<ArrowUp />} disabled={i <= 0} shortcut="Alt+↑" onSelect={() => { close(); p.move(place.accountId, labelId, -1, place.order) }}>
        {t('layout.folderPins.moveUp')}
      </MenuItem>
      <MenuItem icon={<ArrowDown />} disabled={i < 0 || i === place.order.length - 1} shortcut="Alt+↓" onSelect={() => { close(); p.move(place.accountId, labelId, 1, place.order) }}>
        {t('layout.folderPins.moveDown')}
      </MenuItem>
    </>
  )
}

/** Focus a pinned folder again after it moved (React moves the element, and the move drops focus). */
function refocus(accountId: number, labelId: number) {
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-pinned-folder="${accountId}:${labelId}"]`)?.focus())
}

/**
 * A folder row's drag and keys. Every folder row (an account's, or the unified Labels list's) can
 * be dragged; a pinned folder takes drops (pin before or after it) and Alt+↑/↓; a folder under
 * "More folders" takes a pinned folder of its own account (unpin).
 */
export function useFolderRowPins(labelId: number, place?: PinPlace) {
  const p = useAccountPins()
  const canDrag = useCanDragRows()
  const mailDrag = useDragging()
  const drag = useFolderDrag((s) => s.drag)
  const setDrag = useFolderDrag((s) => s.set)
  const [line, setLine] = useState<'before' | 'after' | 'out' | null>(null)
  const props: Record<string, unknown> = {}
  if (place?.pinned) {
    props['data-pinned-folder'] = `${place.accountId}:${labelId}`
    props.onKeyDown = (e: KeyboardEvent) => {
      if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return
      e.preventDefault()
      e.stopPropagation()
      p.move(place.accountId, labelId, e.key === 'ArrowUp' ? -1 : 1, place.order)
      refocus(place.accountId, labelId)
    }
  }
  if (canDrag && !mailDrag) {
    props.draggable = true
    props.onDragStart = (e: DragEvent) => {
      e.stopPropagation()
      e.dataTransfer.setData(FOLDER_DRAG_TYPE, String(labelId))
      e.dataTransfer.effectAllowed = 'move'
      // After the drag has started: changing the page inside dragstart (the "Pin here" line moves
      // the rows below it) can cancel the drag.
      const d = { labelId, accountId: place?.accountId ?? null, pinned: !!place?.pinned }
      window.setTimeout(() => setDrag(d), 0)
    }
    props.onDragEnd = () => {
      setDrag(null)
      setLine(null)
    }
    if (place && drag && drag.labelId !== labelId) {
      const takes = place.pinned ? true : drag.pinned && drag.accountId === place.accountId
      if (takes) {
        props.onDragOver = (e: DragEvent<HTMLElement>) => {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
          if (!place.pinned) return setLine('out')
          const r = e.currentTarget.getBoundingClientRect()
          setLine(e.clientY > r.top + r.height / 2 ? 'after' : 'before')
        }
        props.onDragLeave = () => setLine(null)
        props.onDrop = (e: DragEvent) => {
          e.preventDefault()
          const where = line
          setDrag(null)
          setLine(null)
          if (!place.pinned) return p.unpin(place.accountId, drag.labelId)
          const others = place.order.filter((id) => id !== drag.labelId)
          const before = where === 'after' ? (others[others.indexOf(labelId) + 1] ?? null) : labelId
          p.pin(place.accountId, drag.labelId, before, place.address)
        }
      }
    }
  }
  const className = line === 'out' ? ' drop-over' : line ? ` fav-drop-${line}` : ''
  return { props, className }
}

/** A drop place in an account group while a folder is dragged: "Pin here" (at the end of the
 *  pinned folders) or "More folders" (unpin). */
export function usePinDropZone(accountId: number, address: string, kind: 'pin' | 'unpin') {
  const p = useAccountPins()
  const drag = useFolderDrag((s) => s.drag)
  const setDrag = useFolderDrag((s) => s.set)
  const [over, setOver] = useState(false)
  const takes = !!drag && (kind === 'pin' ? !(drag.pinned && drag.accountId === accountId) : drag.pinned && drag.accountId === accountId)
  if (!takes || !drag) return { active: false, over: false, props: {} }
  return {
    active: true,
    over,
    props: {
      onDragOver: (e: DragEvent) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        setOver(true)
      },
      onDragLeave: () => setOver(false),
      onDrop: (e: DragEvent) => {
        e.preventDefault()
        setOver(false)
        setDrag(null)
        if (kind === 'pin') p.pin(accountId, drag.labelId, null, address)
        else p.unpin(accountId, drag.labelId)
      },
    },
  }
}

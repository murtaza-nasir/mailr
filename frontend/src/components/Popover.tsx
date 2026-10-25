import { Check, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { alignMenuX } from '../lib/floating'
import { isRtl } from '../lib/rtl'
import { GRACE_MS, inTriangle, OPEN_DELAY, placeSubmenu, safeTriangle, submenuKey, type Point } from '../lib/submenu'
import { t as tr } from '../i18n'

export type Placement = 'bottom-start' | 'bottom-end' | 'top-start' | 'top-end' | 'right-start' | 'left-start'

interface PopoverProps {
  anchor: HTMLElement | DOMRect | null
  open: boolean
  onClose: () => void
  placement?: Placement
  children: ReactNode
  className?: string
  /** role for the container: menu (default) enables arrow-key navigation */
  role?: 'menu' | 'dialog' | 'listbox'
  label?: string
  minWidth?: number
  /** Keep focus where it is (e.g. autocomplete). */
  noFocus?: boolean
}

function rectOf(a: HTMLElement | DOMRect): DOMRect {
  return a instanceof HTMLElement ? a.getBoundingClientRect() : a
}

const ITEM_ROLES = ['menuitem', 'menuitemcheckbox', 'menuitemradio']
const ITEMS = ITEM_ROLES.map((r) => `[role="${r}"]:not(:disabled)`).join(', ')
const FIRST_FOCUS = `input, ${ITEMS}, button:not(:disabled)`
/** The first thing to focus in a submenu panel: a field marked data-autofocus (a filter box), else
 *  the first field or item (not Back). */
const PANEL_FOCUS = `input, ${ITEM_ROLES.map((r) => `[role="${r}"]:not(:disabled):not(.submenu-back)`).join(', ')}`

// ---------- submenu plumbing ----------
// A menu (the popover, or an open submenu's panel) is a "level": at most one of its submenus is
// open, and hover intent and the safe triangle live there. The root keeps what the whole menu
// shares: the open submenus innermost last (Escape closes the last), the panels outside the
// popover's own box (a click or scroll in them is not "outside"), and on a phone the slot that
// submenus open in.

interface MenuRoot {
  stack: RefObject<Array<() => void>>
  panels: RefObject<Set<HTMLElement>>
  /** Phone width: a submenu replaces the menu's content in place. */
  inline: boolean
  slot: HTMLElement | null
  setInlineDepth: (f: (n: number) => number) => void
}

interface MenuLevel {
  openKey: string | null
  open: (key: string | null) => void
  /** The pointer is over an item: `key` is its submenu's key, null for a plain item. */
  hover: (key: string | null, p: Point) => void
  cancel: () => void
  /** The pointer left the open submenu's item, perhaps toward its panel. */
  leave: (key: string, p: Point, panel: HTMLElement | null, side: 'right' | 'left') => void
}

const RootCtx = createContext<MenuRoot | null>(null)
const LevelCtx = createContext<MenuLevel | null>(null)

function useMenuLevel(): MenuLevel {
  const [openKey, setOpenKey] = useState<string | null>(null)
  const pending = useRef<{ key: string | null; grace: boolean; t: number } | null>(null)
  const grace = useRef<{ tri: [Point, Point, Point]; until: number; panel: DOMRect; side: 'right' | 'left' } | null>(null)
  const openRef = useRef(openKey)
  useLayoutEffect(() => {
    openRef.current = openKey
  }, [openKey])
  useEffect(() => () => {
    if (pending.current) window.clearTimeout(pending.current.t)
  }, [])
  return useMemo<MenuLevel>(() => {
    const cancel = () => {
      if (pending.current) window.clearTimeout(pending.current.t)
      pending.current = null
    }
    return {
      openKey,
      cancel,
      open: (key) => {
        cancel()
        grace.current = null
        openRef.current = key
        setOpenKey(key)
      },
      hover: (key, p) => {
        if (key === openRef.current || (key === null && openRef.current === null)) {
          cancel()
          return
        }
        // Moving through the safe triangle toward the open panel: wait longer before the item
        // under the pointer takes over (the panel's own pointer events cancel it on arrival).
        const g = grace.current
        const heading = !!g && Date.now() < g.until && inTriangle(p, ...g.tri)
        // Still heading for the panel: the triangle follows the pointer, so a slow, steady move
        // keeps it; stopping on an item lets that item take over after GRACE_MS.
        if (g && heading) {
          g.tri = safeTriangle(p, g.panel, g.side)
          g.until = Date.now() + GRACE_MS
        }
        if (pending.current && pending.current.key === key && pending.current.grace === heading) return
        cancel()
        const t = window.setTimeout(() => {
          pending.current = null
          grace.current = null
          openRef.current = key
          setOpenKey(key)
        }, heading ? GRACE_MS : OPEN_DELAY)
        pending.current = { key, grace: heading, t }
      },
      leave: (key, p, panel, side) => {
        if (pending.current?.key === key) cancel()
        if (openRef.current !== key || !panel) return
        const r = panel.getBoundingClientRect()
        grace.current = { tri: safeTriangle(p, r, side), until: Date.now() + GRACE_MS, panel: r, side }
      },
    }
  }, [openKey])
}

function isMouse(e: ReactPointerEvent) {
  return e.pointerType === 'mouse' || e.pointerType === 'pen'
}

export function Popover(props: PopoverProps) {
  if (!props.open || !props.anchor) return null
  return <OpenPopover {...props} anchor={props.anchor} />
}

function OpenPopover({ anchor, onClose, placement = 'bottom-start', children, className = '', role = 'menu', label, minWidth, noFocus }: PopoverProps & { anchor: HTMLElement | DOMRect }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  // On a phone a menu (or a small dialog such as a picker) is a bottom sheet: in reach of the
  // thumb, above the home indicator, with a scrim. Autocomplete lists (noFocus) stay anchored.
  const sheetScreen = useMediaQuery('(max-width: 600px), (max-width: 900px) and (pointer: coarse)')
  const sheet = sheetScreen && role !== 'listbox' && !noFocus
  const inline = useMediaQuery('(max-width: 600px)') || sheet
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  const [inlineDepth, setInlineDepth] = useState(0)
  const stack = useRef<Array<() => void>>([])
  const panels = useRef<Set<HTMLElement>>(new Set())
  const level = useMenuLevel()
  const root = useMemo<MenuRoot>(() => ({ stack, panels, inline, slot, setInlineDepth }), [inline, slot])

  useLayoutEffect(() => {
    if (!ref.current) return
    if (sheet) {
      setPos({ top: 0, left: 0 })
      return
    }
    const r = rectOf(anchor)
    const el = ref.current
    const w = el.offsetWidth
    const h = el.offsetHeight
    const vw = window.innerWidth
    const vh = window.innerHeight
    let top: number
    let left = 0
    if (placement.startsWith('bottom')) {
      top = r.bottom + 4
      if (top + h > vh - 8 && r.top - h - 4 > 8) top = r.top - h - 4
    } else if (placement.startsWith('top')) {
      top = r.top - h - 4
      if (top < 8) top = r.bottom + 4
    } else {
      top = r.top
    }
    // Start or end aligned: toward the side with room (lib/floating.ts alignMenuX).
    // In a right-to-left page start is the right; a menu placed beside its anchor ('right-start',
    // 'left-start') swaps sides, so it opens toward the end as it does left to right.
    const rtl = isRtl()
    const beside = placement.startsWith('right') ? (rtl ? 'left' : 'right') : placement.startsWith('left') ? (rtl ? 'right' : 'left') : null
    if (placement.endsWith('start') && !beside) left = alignMenuX(r, w, vw, 'start', 8, rtl)
    else if (placement.endsWith('end')) left = alignMenuX(r, w, vw, 'end', 8, rtl)
    else if (beside === 'right') left = r.right + 4
    else if (beside === 'left') left = r.left - w - 4
    left = Math.max(8, Math.min(left, vw - w - 8))
    top = Math.max(8, Math.min(top, vh - h - 8))
    setPos({ top, left })
    // A submenu opened in place (phone width) changes the menu's size: place it again.
  }, [anchor, placement, inlineDepth, sheet])

  useEffect(() => {
    const inside = (t: Node) => !!ref.current?.contains(t) || [...panels.current].some((p) => p.contains(t))
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (inside(t)) return
      if (anchor instanceof HTMLElement && anchor.contains(t)) return
      onClose()
    }
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        // An open submenu closes first, back to the item that opened it.
        const top = stack.current[stack.current.length - 1]
        if (top) top()
        else onClose()
      }
    }
    // Where the anchor was when the menu opened: a scroll closes the menu only when it moved it.
    const at = anchor instanceof HTMLElement ? anchor.getBoundingClientRect() : null
    const onScroll = (e: Event) => {
      if (e.target instanceof Node && inside(e.target)) return
      // Only a scroll that moves the anchor closes the menu: a calendar grid scrolling itself to
      // the working hours after its data loads leaves a menu in the left column where it was, and an
      // email that scrolls to its newest message under a toolbar that stays put keeps the More menu
      // open (a click on an item could otherwise land on nothing).
      if (anchor instanceof HTMLElement && e.target instanceof Node && !e.target.contains(anchor)) return
      if (at && anchor instanceof HTMLElement) {
        const now = anchor.getBoundingClientRect()
        if (Math.abs(now.top - at.top) < 2 && Math.abs(now.left - at.left) < 2) return
      }
      onClose()
    }
    // A sheet stays put when the page resizes (the on-screen keyboard opening) or scrolls behind it.
    const onResize = () => {
      if (!sheet) onClose()
    }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', onResize)
    if (!sheet) document.addEventListener('scroll', onScroll, true)
    return () => {
      document.removeEventListener('mousedown', onDown, true)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', onResize)
      document.removeEventListener('scroll', onScroll, true)
    }
  }, [onClose, anchor, sheet])

  // Closing the menu puts the focus back where it was (the button that opened it), unless
  // the user has moved it somewhere else meanwhile. Submenus restore their own item.
  const opener = useRef<HTMLElement | null>(document.activeElement as HTMLElement | null)
  useLayoutEffect(() => {
    const el = ref.current
    const back = opener.current
    return () => {
      const active = document.activeElement as HTMLElement | null
      const lost = !active || active === document.body || (!!el && el.contains(active))
      if (!lost || !back || back === document.body) return
      // After this commit: the menu's nodes are gone, and an item that opened a dialog has
      // moved the focus into it by then.
      requestAnimationFrame(() => {
        const now = document.activeElement
        if (back.isConnected && (!now || now === document.body)) back.focus({ preventScroll: true })
      })
    }
  }, [])

  const focused = useRef(false)
  useEffect(() => {
    if (noFocus || !pos || focused.current) return
    focused.current = true
    const el = ref.current
    ;(el?.querySelector<HTMLElement>('[data-autofocus]') ?? el?.querySelector<HTMLElement>(FIRST_FOCUS))?.focus({ preventScroll: true })
  }, [noFocus, pos])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    // The menu the focus is in: this one, or a submenu (a panel's events bubble here through React).
    const menu = (e.target as HTMLElement).closest<HTMLElement>('[role="menu"]')
    if (!menu || (role !== 'menu' && menu === ref.current)) return
    const items = Array.from(menu.querySelectorAll<HTMLElement>(ITEMS)).filter(
      (el) => el.closest('[role="menu"]') === menu && el.getClientRects().length > 0,
    )
    if (!items.length) return
    e.preventDefault()
    e.stopPropagation()
    const active = document.activeElement as HTMLElement | null
    const idx = items.indexOf(active as HTMLElement)
    let next: number
    if (idx >= 0) next = e.key === 'ArrowDown' ? (idx + 1) % items.length : (idx - 1 + items.length) % items.length
    else {
      // From a field (a filter box): the nearest item below or above it.
      const after = (el: HTMLElement) => !!active && !!(active.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)
      next = e.key === 'ArrowDown' ? Math.max(0, items.findIndex(after)) : (() => {
        const before = items.filter((el) => !after(el))
        return before.length ? items.indexOf(before[before.length - 1]) : items.length - 1
      })()
    }
    items[next]?.focus()
  }

  return createPortal(
    <RootCtx.Provider value={root}>
      <LevelCtx.Provider value={level}>
        {sheet && <div className="sheet-scrim popover-scrim" aria-hidden />}
        <div
          ref={ref}
          className={`popover ${className}${sheet ? ' as-sheet' : ''}`}
          role={role}
          aria-label={label}
          data-inline-open={inlineDepth > 0 ? 'true' : undefined}
          style={sheet ? undefined : { top: pos?.top ?? -9999, left: pos?.left ?? -9999, minWidth, visibility: pos ? 'visible' : 'hidden' }}
          onKeyDown={onKeyDown}
        >
          {sheet && <span className="sheet-handle" aria-hidden />}
          {children}
          <div className="menu-inline-slot" ref={setSlot} />
        </div>
      </LevelCtx.Provider>
    </RootCtx.Provider>,
    document.body,
  )
}

interface MenuItemProps {
  icon?: ReactNode
  children: ReactNode
  onSelect: () => void
  /** A key hint, shown only where a keyboard is likely (hidden on touch and phone widths). */
  shortcut?: string
  /** The hint is text typed in the editor (";sig"), not a key, so it shows on touch too. */
  typed?: boolean
  disabled?: boolean
  checked?: boolean | 'mixed'
  /** One of a set (a radio choice): the current one shows a check mark. */
  selected?: boolean
  danger?: boolean
  /** Looks disabled (aria-disabled) but still takes a click, so a tap can say why (`tip`). */
  dimmed?: boolean
  /** A tooltip (the app's data-tip). */
  tip?: string
}

export function MenuItem({ icon, children, onSelect, shortcut, typed, disabled, checked, selected, danger, dimmed, tip }: MenuItemProps) {
  const level = useContext(LevelCtx)
  const isCheck = checked !== undefined
  const isRadio = selected !== undefined
  return (
    <button
      type="button"
      className="menu-item"
      role={isCheck ? 'menuitemcheckbox' : isRadio ? 'menuitemradio' : 'menuitem'}
      aria-checked={isCheck ? (checked === 'mixed' ? 'mixed' : checked) : isRadio ? selected : undefined}
      aria-disabled={dimmed || undefined}
      data-tip={tip}
      disabled={disabled}
      onClick={onSelect}
      // Resting on a plain item closes a sibling's open submenu (after the hover delay).
      onPointerMove={level ? (e) => { if (isMouse(e)) level.hover(null, { x: e.clientX, y: e.clientY }) } : undefined}
      style={danger ? { color: 'var(--danger)' } : undefined}
    >
      {isCheck && (
        <input
          type="checkbox"
          className="checkbox"
          tabIndex={-1}
          readOnly
          checked={checked === true}
          ref={(el) => {
            if (el) el.indeterminate = checked === 'mixed'
          }}
        />
      )}
      {icon}
      <span className="ellipsis">{children}</span>
      {shortcut && <span className={typed ? 'shortcut typed' : 'shortcut'}>{shortcut}</span>}
      {isRadio && <Check className={`menu-check${selected ? '' : ' off'}`} aria-hidden />}
    </button>
  )
}

interface SubMenuProps {
  icon?: ReactNode
  /** The item's text; also the panel's name and, on a phone, its header. */
  label: string
  /** A key that opens the standalone menu from the list (a hint, as on MenuItem). */
  shortcut?: string
  disabled?: boolean
  /** The panel's content, mounted only while it is open. */
  children: ReactNode
  minWidth?: number
  /** "dialog" for a panel that is not a list of items (a colour grid). */
  panelRole?: 'menu' | 'dialog'
  /** A tooltip on the item (the app's data-tip). */
  tip?: string
}

/**
 * An item that opens a submenu beside its menu. It
 * opens on hover (after OPEN_DELAY, with a safe triangle toward the panel), on click or tap, and
 * with → / Enter / Space; ← or Escape closes it back to this item. The panel flips to the left
 * with no room on the right, shifts up near the bottom and scrolls when tall. At phone width it
 * replaces the menu's content in place, under a Back item.
 */
export function SubMenu({ icon, label, shortcut, disabled, children, minWidth = 220, panelRole = 'menu', tip }: SubMenuProps) {
  const root = useContext(RootCtx)
  const parent = useContext(LevelCtx)
  const key = useId()
  const panelId = `${key}-panel`
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const side = useRef<'right' | 'left'>('right')
  const focusOnOpen = useRef(false)
  const open = !!parent && parent.openKey === key
  const inline = !!root?.inline

  const show = (focus: boolean) => {
    if (disabled || !parent) return
    focusOnOpen.current = focus
    parent.open(key)
  }
  const close = useCallback(() => {
    const had = !!panel.current?.contains(document.activeElement)
    parent?.open(null)
    if (had || inline) trigger.current?.focus({ preventScroll: true })
  }, [parent, inline])

  // While open, Escape (handled by the root) closes this one first.
  useEffect(() => {
    if (!open || !root) return
    const stack = root.stack.current
    stack.push(close)
    return () => {
      const i = stack.lastIndexOf(close)
      if (i >= 0) stack.splice(i, 1)
    }
  }, [open, root, close])

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="menu-item submenu-trigger"
        role="menuitem"
        aria-haspopup={panelRole}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        data-active={open ? 'true' : undefined}
        data-tip={tip}
        disabled={disabled}
        onClick={(e) => show(e.detail === 0 || inline)}
        onKeyDown={(e) => {
          if (submenuKey(e.key, 'trigger', 'item', isRtl()) === 'open') {
            e.preventDefault()
            e.stopPropagation()
            show(true)
          }
        }}
        onPointerMove={(e) => {
          if (!inline && isMouse(e) && !disabled) parent?.hover(key, { x: e.clientX, y: e.clientY })
        }}
        onPointerLeave={(e) => {
          if (!inline && isMouse(e)) parent?.leave(key, { x: e.clientX, y: e.clientY }, panel.current, side.current)
        }}
      >
        {icon}
        <span className="ellipsis">{label}</span>
        {shortcut && <span className="shortcut">{shortcut}</span>}
        <ChevronRight className="submenu-chevron" aria-hidden />
      </button>
      {open && root && parent && (
        <SubPanel id={panelId} label={label} role={panelRole} minWidth={minWidth} root={root} trigger={trigger}
          panel={panel} side={side} focusOnOpen={focusOnOpen} parent={parent} onClose={close}>
          {children}
        </SubPanel>
      )}
    </>
  )
}

interface SubPanelProps {
  id: string
  label: string
  role: 'menu' | 'dialog'
  minWidth: number
  root: MenuRoot
  trigger: RefObject<HTMLButtonElement | null>
  panel: RefObject<HTMLDivElement | null>
  side: RefObject<'right' | 'left'>
  focusOnOpen: RefObject<boolean>
  parent: MenuLevel
  onClose: () => void
  children: ReactNode
}

function SubPanel({ id, label, role, minWidth, root, trigger, panel, side, focusOnOpen, parent, onClose, children }: SubPanelProps) {
  const level = useMenuLevel()
  const inline = root.inline
  const [pos, setPos] = useState<{ left: number; top: number; maxHeight: number; side: 'right' | 'left' } | null>(null)

  // Placed beside its item, and again whenever its content changes size (a filter, a late load).
  useLayoutEffect(() => {
    if (inline) return
    const el = panel.current
    const t = trigger.current
    if (!el || !t) return
    const place = () => {
      const menu = t.closest<HTMLElement>('.popover') ?? t
      const p = placeSubmenu(t.getBoundingClientRect(), menu.getBoundingClientRect(),
        { width: el.offsetWidth, height: el.scrollHeight }, { width: window.innerWidth, height: window.innerHeight }, isRtl())
      side.current = p.side
      setPos((o) => (o && o.left === p.left && o.top === p.top && o.maxHeight === p.maxHeight ? o : p))
    }
    place()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(place) : null
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [inline, panel, trigger, side])

  // Registered with the root, so a click or scroll inside is not "outside the menu".
  useLayoutEffect(() => {
    const el = panel.current
    if (!el || inline) return
    const set = root.panels.current
    set.add(el)
    return () => {
      set.delete(el)
    }
  }, [root, panel, inline])

  // On a phone the menu's own items hide while a submenu shows in their place.
  useEffect(() => {
    if (!inline) return
    root.setInlineDepth((n) => n + 1)
    return () => root.setInlineDepth((n) => n - 1)
  }, [inline, root])

  const ready = inline || !!pos
  useEffect(() => {
    if (!ready || !(focusOnOpen.current || inline)) return
    focusOnOpen.current = false
    const el = panel.current
    ;(el?.querySelector<HTMLElement>('[data-autofocus]') ?? el?.querySelector<HTMLElement>(PANEL_FOCUS) ?? el)?.focus({ preventScroll: true })
  }, [ready, inline, focusOnOpen, panel])

  // A panel that closes with the focus inside gives it back to its item.
  useLayoutEffect(() => {
    const el = panel.current
    const t = trigger.current
    return () => {
      if (el?.contains(document.activeElement)) t?.focus({ preventScroll: true })
    }
  }, [panel, trigger])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement
    // Only this panel's own keys: a nested panel's bubble through here too.
    if (t.closest('.submenu-panel') !== panel.current) return
    // Fields and composite widgets (a filter box, a colour grid, tabs) keep their own arrow keys.
    const target = t.matches('input, textarea, select') || t.closest('[role="grid"], [role="tablist"], [role="radiogroup"]') ? 'field' : 'item'
    if (submenuKey(e.key, 'panel', target, isRtl()) === 'close' && e.key !== 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
  }

  const body = (
    <LevelCtx.Provider value={level}>
      <div
        ref={panel}
        id={id}
        className={`submenu-panel${inline ? ' submenu-inline' : ' popover'}`}
        role={role}
        aria-label={label}
        tabIndex={-1}
        data-side={inline ? undefined : pos?.side}
        style={inline ? undefined : {
          top: pos?.top ?? -9999, left: pos?.left ?? -9999, maxHeight: pos?.maxHeight, minWidth,
          visibility: pos ? 'visible' : 'hidden',
        }}
        onKeyDown={onKeyDown}
        onPointerEnter={() => parent.cancel()}
        onPointerMove={() => parent.cancel()}
      >
        {inline && (
          <>
            <button type="button" className="menu-item submenu-back" role="menuitem" onClick={onClose}>
              <ChevronLeft aria-hidden />
              <span className="ellipsis">{tr('common.back')}</span>
            </button>
            <div className="menu-header">{label}</div>
          </>
        )}
        {children}
      </div>
    </LevelCtx.Provider>
  )
  if (inline) return root.slot ? createPortal(body, root.slot) : null
  return createPortal(body, document.body)
}

export function MenuSep() {
  return <div className="menu-sep" role="separator" />
}

export function MenuHeader({ children }: { children: ReactNode }) {
  return <div className="menu-header">{children}</div>
}

/** Convenience: state for an anchored popover. */
export function useAnchor<T extends HTMLElement = HTMLElement>() {
  const [anchor, setAnchor] = useState<T | DOMRect | null>(null)
  return {
    anchor,
    open: anchor !== null,
    toggle: (el: T | DOMRect) => setAnchor((a) => (a ? null : el)),
    show: (el: T | DOMRect) => setAnchor(el),
    close: () => setAnchor(null),
  }
}

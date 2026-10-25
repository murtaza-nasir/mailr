// ACT mail.copy_address @web.reader: copy a sender's or recipient's address from the open message.
// A small copy button beside the address appears on hover or keyboard focus (Shift+click copies
// "Name <address>"). On touch, where nothing hovers, a long-press on the name or the address opens
// a menu: Copy address, Copy name, Copy name and address.
import { Copy } from 'lucide-react'
import { useRef, useState, type HTMLAttributes, type MouseEvent, type TouchEvent, type ReactNode, type SyntheticEvent } from 'react'
import type { Address } from '../../api/types'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { addressName, copyText, nameAndAddress } from '../../lib/clipboard'
import { personName } from '../../lib/format'
import { safeAddress } from '../../lib/safeText'
import { t } from '../../i18n'

/** How long a finger rests on a name or address before its copy menu opens (as DeadlineChip). */
const LONG_PRESS_MS = 500
/** A finger that moves further than this is scrolling, not pressing. */
const MOVE_SLOP = 10

const copied = () => t('conversation.addressCopy.copied')

export function copyAddress(a: Address) {
  // Never a bidi override or an invisible character on the clipboard.
  return copyText(safeAddress(a.email), copied())
}
export function copyName(a: Address) {
  return copyText(personName(addressName(a)), copied())
}
export function copyNameAndAddress(a: Address) {
  return copyText(nameAndAddress(a), copied())
}

/** What the button and the menus call this person: the name, or the address without one. */
function label(a: Address): string {
  return personName(addressName(a)) || a.email
}

/** The copy button beside an address. It keeps its room when hidden, so showing it moves nothing. */
export function AddressCopyButton({ addr }: { addr: Address }) {
  return (
    <button
      type="button"
      className="addr-copy icon-btn"
      aria-label={t('conversation.addressCopy.copyAddressOf', { name: label(addr) })}
      data-tip={t('conversation.addressCopy.copyAddress')}
      data-testid="addr-copy"
      onClick={(e) => {
        e.stopPropagation()
        void (e.shiftKey ? copyNameAndAddress(addr) : copyAddress(addr))
      }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <Copy aria-hidden="true" />
    </button>
  )
}

/** The menu's three choices (the long-press menu; the same as the Android reader's). */
export function AddressCopyItems({ addr, onDone }: { addr: Address; onDone: () => void }) {
  const hasName = !!addressName(addr)
  return (
    <>
      <MenuItem onSelect={() => { onDone(); void copyAddress(addr) }}>{t('conversation.addressCopy.copyAddress')}</MenuItem>
      {hasName && <MenuItem onSelect={() => { onDone(); void copyName(addr) }}>{t('conversation.addressCopy.copyName')}</MenuItem>}
      {hasName && <MenuItem onSelect={() => { onDone(); void copyNameAndAddress(addr) }}>{t('conversation.addressCopy.copyNameAndAddress')}</MenuItem>}
    </>
  )
}

/**
 * Long-press on touch: `press(addr)` goes on a name or an address, and `menu` once per message,
 * outside any click handler and outside other popovers (a menu inside the details popover would
 * close with it on the first tap). A press that opened the menu swallows the click that follows it
 * (the header would otherwise collapse the message).
 */
export function useAddressMenu(): { press: (addr: Address) => HTMLAttributes<HTMLElement>; menu: ReactNode } {
  const pop = useAnchor()
  const [addr, setAddr] = useState<Address | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pressed = useRef(false)
  const origin = useRef<{ x: number; y: number } | null>(null)
  const clear = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
  }
  const open = (a: Address, el: HTMLElement) => {
    clear()
    pressed.current = true
    setAddr(a)
    pop.show(el)
  }
  // Touch events, not pointer events: the browser cancels a resting pointer when its own
  // long-press starts (text selection), and a touch keeps going.
  const press = (a: Address): HTMLAttributes<HTMLElement> => ({
    onTouchStart: (e: TouchEvent<HTMLElement>) => {
      if (e.touches.length !== 1) return clear()
      const el = e.currentTarget
      origin.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }
      pressed.current = false
      clear()
      timer.current = setTimeout(() => open(a, el), LONG_PRESS_MS)
    },
    onTouchMove: (e: TouchEvent<HTMLElement>) => {
      const o = origin.current
      const t = e.touches[0]
      if (timer.current && o && t && Math.hypot(t.clientX - o.x, t.clientY - o.y) > MOVE_SLOP) clear()
    },
    onTouchEnd: (e: TouchEvent<HTMLElement>) => {
      clear()
      // The finger lifting after the menu opened is no tap: no mousedown or click follows it (a
      // mousedown would land on the menu's scrim and close it again).
      if (pressed.current && e.cancelable) e.preventDefault()
    },
    onTouchCancel: clear,
    onClickCapture: (e: MouseEvent<HTMLElement>) => {
      if (!pressed.current) return
      pressed.current = false
      e.stopPropagation()
      e.preventDefault()
    },
    // The browser's own long-press menu (text selection, "Copy") would open in place of ours: a
    // touch that is still resting opens ours now.
    onContextMenu: (e: MouseEvent<HTMLElement>) => {
      if (pressed.current) e.preventDefault()
      else if (timer.current) {
        e.preventDefault()
        open(a, e.currentTarget)
      }
    },
  })
  const stop = (e: SyntheticEvent) => e.stopPropagation()
  const menu = (
    // React events bubble out of the portalled menu; they end here so a choice does nothing else.
    <span hidden={!pop.open} onClick={stop} onMouseDown={stop} onPointerDown={stop} onContextMenu={stop}>
      {addr && (
        <Popover anchor={pop.anchor} open={pop.open} onClose={pop.close} label={label(addr)} minWidth={220}>
          <AddressCopyItems addr={addr} onDone={pop.close} />
        </Popover>
      )}
    </span>
  )
  return { press, menu }
}

/** One address in the details popover: the text and its copy button. */
export function CopyableAddress({ addr, press, children }: { addr: Address; press: HTMLAttributes<HTMLElement>; children: ReactNode }) {
  return (
    <span className="addr-item">
      <bdi className="addr-text" {...press}>{children}</bdi>
      <AddressCopyButton addr={addr} />
    </span>
  )
}

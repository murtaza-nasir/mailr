// A modal bottom sheet for phones: it rises from the bottom edge
// above the home indicator, a drag on its handle (or a tap on the scrim, or Escape) closes it.
// The install guide, More, Pending changes and, on touch phones, every menu use it.
import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { t } from '../i18n'

interface BottomSheetProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  /** The sheet's name for screen readers when it has no visible title. */
  label?: string
  className?: string
  /** Extra attribute for tests and styling. */
  testId?: string
}

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

export function BottomSheet({ open, onClose, title, children, label, className = '', testId }: BottomSheetProps) {
  if (!open) return null
  return <OpenSheet onClose={onClose} title={title} label={label} className={className} testId={testId}>{children}</OpenSheet>
}

function OpenSheet({ onClose, title, children, label, className, testId }: Omit<BottomSheetProps, 'open'>) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    const first = ref.current?.querySelector<HTMLElement>('[data-autofocus]')
    if (first) first.focus()
    else ref.current?.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeRef.current()
      } else if (e.key === 'Tab' && ref.current) {
        const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((x) => x.offsetParent !== null)
        if (!items.length) return
        const first = items[0]
        const last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      prev?.focus?.({ preventScroll: true })
    }
  }, [])

  // Drag the handle (or the header) down to close.
  const drag = useRef<{ y: number; dy: number } | null>(null)
  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { y: e.clientY, dy: 0 }
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    const el = ref.current
    if (!d || !el) return
    d.dy = Math.max(0, e.clientY - d.y)
    el.style.transform = `translateY(${d.dy}px)`
  }
  const onPointerUp = () => {
    const d = drag.current
    const el = ref.current
    drag.current = null
    if (!el || !d) return
    el.style.transform = ''
    if (d.dy > Math.min(120, el.offsetHeight / 3)) closeRef.current()
  }

  return createPortal(
    <div className="sheet-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-testid={testId ? `${testId}-scrim` : undefined}>
      <div
        ref={ref}
        className={`bottom-sheet ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={title ? undefined : label}
        tabIndex={-1}
        data-testid={testId}
      >
        <div className="sheet-grab" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
          <span className="sheet-handle" aria-hidden />
          {title && (
            <div className="sheet-head">
              <h2 id={titleId}>{title}</h2>
              <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onClose} onPointerDown={(e) => e.stopPropagation()}>
                <X size={20} />
              </button>
            </div>
          )}
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

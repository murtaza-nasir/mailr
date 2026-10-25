import { X } from 'lucide-react'
import { useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useModalFocus } from '../hooks/useModalFocus'
import { t } from '../i18n'

interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  footer?: ReactNode
  wide?: boolean
  /** Prevent closing via scrim click (for forms with unsaved input). */
  modal?: boolean
  initialFocus?: string // CSS selector
}

export function Dialog({ open, onClose, title, children, footer, wide, modal, initialFocus }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useModalFocus(ref, open, onClose, { initialFocus })

  if (!open) return null
  return createPortal(
    <div
      className="scrim"
      onMouseDown={(e) => {
        if (!modal && e.target === e.currentTarget) onClose()
      }}
    >
      <div ref={ref} className={`dialog${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="dialog-head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn sm" aria-label={t('common.close')} data-tip={t('common.close')} onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

interface ConfirmProps {
  open: boolean
  title: string
  body: ReactNode
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
  busy?: boolean
  /** The confirm button takes the focus, so Enter confirms. For a step the user just asked for
   * that has an Undo (a section's Archive all); the default keeps the focus on Cancel. */
  focusConfirm?: boolean
}

export function ConfirmDialog({ open, title, body, confirmLabel = 'OK', danger, onConfirm, onClose, busy, focusConfirm }: ConfirmProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      // The safe choice has the focus, not the close button: Enter never confirms by accident.
      initialFocus={focusConfirm ? '[data-confirm-ok]' : '[data-confirm-cancel]'}
      footer={
        <>
          <button type="button" className="btn text" data-confirm-cancel onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className={`btn ${danger ? 'danger' : 'filled'}`} data-confirm-ok onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </>
      }
    >
      <div style={{ color: 'var(--text-2)' }}>{body}</div>
    </Dialog>
  )
}

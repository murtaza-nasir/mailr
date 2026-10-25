// A narrow-screen drawer that is a modal dialog: role=dialog with aria-modal, the focus
// moves in and stays in (Tab wraps), Escape and the scrim close it, and the focus returns to the
// button that opened it. Global shortcuts stand down while it is open (data-modal-drawer).
import { useRef, type ReactNode } from 'react'
import { useModalFocus } from '../hooks/useModalFocus'

export function ModalDrawer({ className, scrimClass, label, onClose, children }: {
  className: string
  scrimClass: string
  label: string
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useModalFocus(ref, true, onClose)
  return (
    <div className={scrimClass} onClick={onClose}>
      <div ref={ref} className={className} role="dialog" aria-modal="true" data-modal-drawer aria-label={label} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  )
}

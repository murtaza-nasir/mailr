import { X } from 'lucide-react'
import { useUI } from '../stores/ui'
import { t as tr } from '../i18n'

// Errors are announced at once (role=alert) and the rest politely (role=status): each toast
// sits in the region of its kind. The timers stop while the pointer or the focus is on a toast,
// so Undo cannot expire while you reach for it.
export function Toasts() {
  const toasts = useUI((s) => s.toasts)
  const dismiss = useUI((s) => s.dismissToast)
  const pause = useUI((s) => s.pauseToasts)
  const item = (t: (typeof toasts)[number], order: number) => (
    // `order` keeps the stack in time order across the two regions (they draw no box).
    <div key={t.id} className={`toast${t.error ? ' error' : ''}`} style={{ order }}>
      <span className="toast-msg">{t.message}</span>
      {t.actionLabel && t.onAction && (
        <button
          type="button"
          className="toast-act"
          onClick={() => {
            t.onAction?.()
            dismiss(t.id)
          }}
        >
          {t.actionLabel}
        </button>
      )}
      {t.secondaryLabel && t.onSecondary && (
        <button
          type="button"
          className="toast-act"
          onClick={() => {
            t.onSecondary?.()
            dismiss(t.id)
          }}
        >
          {t.secondaryLabel}
        </button>
      )}
      <button type="button" className="icon-btn" aria-label={tr('common.dismiss')} onClick={() => dismiss(t.id)}>
        <X size={18} />
      </button>
    </div>
  )
  return (
    <div
      className="toasts no-print"
      onMouseEnter={() => pause(true)}
      onMouseLeave={() => pause(false)}
      onFocus={() => pause(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) pause(false)
      }}
    >
      {/* Both regions are always in the page, so a screen reader hears what is added to them. */}
      <div className="toast-region" role="alert">
        {toasts.map((t, i) => (t.error ? item(t, i) : null))}
      </div>
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((t, i) => (t.error ? null : item(t, i)))}
      </div>
    </div>
  )
}

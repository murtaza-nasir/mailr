// The quick-add line for events outside the Calendar app (the calendar panel): one line through the
// grammar, the parse shown as chips under it before Enter. Enter saves; Ctrl/⌘+Enter or
// Shift+Enter opens the full editor with the line's values; Esc clears.

import { CalendarPlus } from 'lucide-react'
import { useMemo, useState } from 'react'
import { parseLine, quickChips, type QuickCtx } from './quickCreate'
import type { QuickResult } from '../../lib/quickadd'
import { t } from '../../i18n'

export function QuickLine({ ctx, placeholder, busy, onSave }: {
  ctx: QuickCtx
  placeholder: string
  busy?: boolean
  /** `editor`: open the full editor instead of saving. Resolves true when the line may clear. */
  onSave: (r: QuickResult, editor: boolean) => Promise<boolean>
}) {
  const [text, setText] = useState('')
  const r = useMemo(() => parseLine(text, 'event', ctx), [text, ctx])
  const chips = useMemo(() => (text.trim() ? quickChips(r, 'event', ctx) : []), [text, r, ctx])
  const submit = async (editor: boolean) => {
    if (!text.trim() || busy) return
    if (await onSave(r, editor)) setText('')
  }
  return (
    <div className="ql">
      <div className="ql-line">
        <CalendarPlus size={18} aria-hidden />
        <input
          className="ql-input"
          value={text}
          placeholder={placeholder}
          aria-label={t('common.newEvent')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void submit(e.ctrlKey || e.metaKey || e.shiftKey)
            } else if (e.key === 'Escape' && text) {
              e.stopPropagation()
              setText('')
            }
          }}
        />
      </div>
      {chips.length > 0 && (
        <div className="ql-chips" aria-live="polite">
          {chips.map((c) => <span key={c} className="ql-chip">{c}</span>)}
          <span className="ql-hint">{t('rail.quickLine.enterToSaveShiftEnter')}</span>
        </div>
      )}
    </div>
  )
}

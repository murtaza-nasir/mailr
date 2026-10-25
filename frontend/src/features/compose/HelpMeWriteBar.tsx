import { Loader2, Sparkles, X } from 'lucide-react'
import type { RefObject } from 'react'
import type { useSkillPicker } from '../rail/assistant/SkillPicker'
import { t } from '../../i18n'

interface Props {
  text: string
  setText: (v: string) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
  picker: ReturnType<typeof useSkillPicker>
  busy: boolean
  /** The draft has text (the prompt then asks what should change). */
  hasBody: boolean
  isReply: boolean
  onRun: () => void
  onClose: () => void
}

/** The Help me write prompt above the compose form's body. */
export function HelpMeWriteBar({ text, setText, inputRef, picker, busy, hasBody, isReply, onRun, onClose }: Props) {
  return (
    <div className="ai-bar">
      <Sparkles size={18} />
      {picker.popover}
      <textarea
        ref={inputRef}
        autoFocus
        rows={1}
        value={text}
        placeholder={hasBody ? t('compose.helpMeWriteBar.whatShouldChangeEG') : isReply ? t('compose.helpMeWriteBar.describeYourReplyEG') : t('compose.helpMeWriteBar.describeTheEmailYouWant')}
        aria-autocomplete="list"
        aria-expanded={picker.open}
        onSelect={picker.sync}
        onChange={(e) => {
          setText(e.target.value)
          picker.sync()
          // Grow with the text, up to about six lines.
          const el = e.target
          el.style.height = 'auto'
          el.style.height = `${Math.min(el.scrollHeight, 150)}px`
        }}
        onKeyDown={(e) => {
          if (picker.onKeyDown(e)) return
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            // Enter and Ctrl/⌘+Enter run the prompt here; they must not reach the compose
            // window's Ctrl+Enter = Send.
            e.preventDefault()
            e.stopPropagation()
            onRun()
          }
          if (e.key === 'Escape') {
            e.stopPropagation()
            onClose()
          }
        }}
        aria-label={t('compose.helpMeWriteBar.instructionsForHelpMeWrite')}
        name="mailr-help-me-write"
        autoComplete="off"
        spellCheck
        // Keep password managers from treating the prompt as a credential field.
        data-1p-ignore=""
        data-lpignore="true"
        data-bwignore="true"
        data-form-type="other"
      />
      <button type="button" className="btn filled sm" disabled={!text.trim() || busy} onClick={onRun}>
        {busy ? <Loader2 style={{ animation: 'spin 0.9s linear infinite' }} /> : t('common.create')}
      </button>
      <button type="button" className="icon-btn sm" aria-label={t('compose.helpMeWriteBar.closeHelpMeWrite')} onClick={onClose}>
        <X size={18} />
      </button>
    </div>
  )
}

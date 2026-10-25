import { ArrowUpRight, CalendarClock, ChevronDown, Clock, FileText, ImagePlus, Paperclip, PenLine, Send, Sparkles, Trash2, Type } from 'lucide-react'
import { useRef, useState, type RefObject } from 'react'
import type { Editor as TiptapEditor } from '@tiptap/react'
import type { Template } from '../../api/types'
import { ConfirmDialog } from '../../components/Dialog'
import { MenuHeader, MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { formatOptionTime, scheduleOptions } from '../../lib/snooze'
import { DateTimeDialog } from '../mail/menus'
import { InsertTimesButton } from '../conversation/InsertTimes'
import { InsertRecordingButton } from '../speakr/InsertRecording'
import { INLINE_ACCEPT } from './inlineImages'
import { t as tr } from '../../i18n'

/** A send the form held back to ask first (no subject or text, or "attached" with nothing attached). */
export type HeldSend = { at?: Date } | null

interface Props {
  editor: TiptapEditor | null
  sending: boolean
  /** Send now, or at `at`; `force` skips the questions about an empty or attachment-less message. */
  onSend: (at?: Date, force?: boolean) => void
  confirmEmpty: HeldSend
  setConfirmEmpty: (v: HeldSend) => void
  confirmAttach: HeldSend
  setConfirmAttach: (v: HeldSend) => void
  showFormat: boolean
  onToggleFormat: () => void
  onAddFiles: (files: File[]) => void
  onInsertImages: (files: File[]) => void
  /** The hidden input the image toolbar's Replace opens, and what it does with the file. */
  replaceRef: RefObject<HTMLInputElement | null>
  onReplaceFile: (f: File) => void
  templates: Template[] | undefined
  onInsertTemplate: (t: Template) => void
  onManageTemplates: () => void
  hasSignature: boolean
  includeSig: boolean
  onToggleSig: () => void
  aiOpen: boolean
  onToggleAi: () => void
  /** Pop the inline reply out into a window; absent when the form cannot. */
  onPopOut?: () => void
  saveState: 'idle' | 'saving' | 'saved' | 'error'
  saveError: string
  onDiscard: () => void
}

/** The compose form's send bar: Send and schedule, the tools (format, attach, image, templates,
 *  times, recordings, signature, Help me write, pop out), the save state and Discard. */
export function ComposeFooter(props: Props) {
  const { editor, sending, onSend, confirmEmpty, setConfirmEmpty, confirmAttach, setConfirmAttach, showFormat, onToggleFormat, onAddFiles, onInsertImages, replaceRef, onReplaceFile, templates, onInsertTemplate, onManageTemplates, hasSignature, includeSig, onToggleSig, aiOpen, onToggleAi, onPopOut, saveState, saveError, onDiscard } = props
  const [pickTime, setPickTime] = useState(false)
  const scheduleMenu = useAnchor()
  const templateMenu = useAnchor()
  const fileRef = useRef<HTMLInputElement>(null)
  const imageRef = useRef<HTMLInputElement>(null)
  return (
    <div className="compose-foot">
      <div className="send-split">
        <button type="button" className="main-btn" disabled={sending} onClick={() => onSend()} data-tip={tr('compose.composeFooter.sendCtrlEnter')}>
          {sending ? tr('common.sending') : tr('common.send')}
        </button>
        <button type="button" className="more-btn" aria-label={tr('compose.composeFooter.moreSendOptions')} data-tip={tr('compose.composeFooter.moreSendOptions')} disabled={sending} onClick={(e) => scheduleMenu.toggle(e.currentTarget)}>
          <ChevronDown size={18} />
        </button>
      </div>
      <Popover anchor={scheduleMenu.anchor} open={scheduleMenu.open} onClose={scheduleMenu.close} placement="top-start" minWidth={300}>
        <MenuHeader>{tr('compose.composeFooter.scheduleSend')}</MenuHeader>
        {scheduleOptions().map((o) => (
          <MenuItem key={o.id} icon={<Clock />} shortcut={formatOptionTime(o.at)} onSelect={() => { scheduleMenu.close(); onSend(o.at) }}>
            {o.label}
          </MenuItem>
        ))}
        <MenuSep />
        <MenuItem icon={<CalendarClock />} onSelect={() => { scheduleMenu.close(); setPickTime(true) }}>
          {tr('compose.composeFooter.pickDateTime')}
        </MenuItem>
        <MenuItem icon={<Send />} onSelect={() => { scheduleMenu.close(); onSend() }}>
          {tr('common.sendNow')}
        </MenuItem>
      </Popover>
      <ConfirmDialog
        open={!!confirmEmpty}
        title={tr('compose.composeFooter.sendWithoutASubject')}
        body={tr('compose.composeFooter.sendThisMessageWithoutA')}
        confirmLabel={tr('common.send')}
        onClose={() => setConfirmEmpty(null)}
        onConfirm={() => {
          const at = confirmEmpty?.at
          setConfirmEmpty(null)
          onSend(at, true)
        }}
      />
      <ConfirmDialog
        open={!!confirmAttach}
        title={tr('compose.composeFooter.sendWithoutAnAttachment')}
        body={tr('compose.composeFooter.youMentionAnAttachmentBut')}
        confirmLabel={tr('compose.composeFooter.sendAnyway')}
        onClose={() => setConfirmAttach(null)}
        onConfirm={() => {
          const at = confirmAttach?.at
          setConfirmAttach(null)
          onSend(at, true)
        }}
      />
      <DateTimeDialog open={pickTime} title={tr('compose.composeFooter.pickDateTime')} confirmLabel={tr('compose.composeFooter.scheduleSend')} onClose={() => setPickTime(false)} onPick={(d) => { setPickTime(false); onSend(d) }} />
      <button type="button" className={`icon-btn${showFormat ? ' active' : ''}`} aria-label={tr('compose.composeFooter.formattingOptions')} data-tip={tr('compose.composeFooter.formattingOptions')} aria-pressed={showFormat} onClick={onToggleFormat}>
        <Type />
      </button>
      <button type="button" className="icon-btn" aria-label={tr('compose.composeFooter.attachFiles')} data-tip={tr('compose.composeFooter.attachFiles')} onClick={() => fileRef.current?.click()}>
        <Paperclip />
      </button>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(e) => {
          onAddFiles(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />
      <button type="button" className="icon-btn" aria-label={tr('compose.composeFooter.insertImage')} data-tip={tr('compose.composeFooter.insertImageOrPasteOr')} onMouseDown={(e) => e.preventDefault()} onClick={() => imageRef.current?.click()}>
        <ImagePlus />
      </button>
      <input
        ref={imageRef}
        type="file"
        accept={INLINE_ACCEPT}
        multiple
        hidden
        aria-label={tr('compose.composeFooter.imageToInsert')}
        onChange={(e) => {
          onInsertImages(Array.from(e.target.files ?? []))
          e.target.value = ''
        }}
      />
      <input
        ref={replaceRef}
        type="file"
        accept={INLINE_ACCEPT}
        hidden
        aria-label={tr('compose.composeFooter.replacementImage')}
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onReplaceFile(f)
          e.target.value = ''
        }}
      />
      <button type="button" className={`icon-btn${templateMenu.open ? ' active' : ''}`} aria-label={tr('common.templates')} data-tip={tr('compose.composeFooter.templatesTypeShortcutInThe')} aria-haspopup="menu" onMouseDown={(e) => e.preventDefault()} onClick={(e) => templateMenu.toggle(e.currentTarget)}>
        <FileText />
      </button>
      <Popover anchor={templateMenu.anchor} open={templateMenu.open} onClose={templateMenu.close} placement="top-start" minWidth={260} label={tr('common.templates')}>
        <MenuHeader>{tr('common.templates')}</MenuHeader>
        {(templates ?? []).length === 0 && <div className="menu-item muted" style={{ cursor: 'default' }}>{tr('compose.composeFooter.noTemplatesYet')}</div>}
        {(templates ?? []).map((t) => (
          <MenuItem key={t.id} icon={<FileText />} shortcut={t.shortcut ? `;${t.shortcut}` : undefined} typed onSelect={() => { templateMenu.close(); onInsertTemplate(t) }}>
            {t.name}
          </MenuItem>
        ))}
        <MenuSep />
        <MenuItem onSelect={() => { templateMenu.close(); onManageTemplates() }}>{tr('compose.composeFooter.manageTemplates')}</MenuItem>
      </Popover>
      <InsertTimesButton editor={editor} />
      <InsertRecordingButton editor={editor} />
      {hasSignature && (
        <button type="button" className={`icon-btn${includeSig ? ' active' : ''}`} aria-label={tr('compose.composeFooter.insertSignature')} data-tip={includeSig ? tr('compose.composeFooter.removeSignature') : tr('compose.composeFooter.insertSignature')} aria-pressed={includeSig} onClick={onToggleSig}>
          <PenLine />
        </button>
      )}
      <button type="button" className={`icon-btn${aiOpen ? ' active' : ''}`} aria-label={tr('compose.composeFooter.helpMeWrite')} data-tip={tr('compose.composeFooter.helpMeWrite')} onClick={onToggleAi}>
        <Sparkles />
      </button>
      {onPopOut && (
        <button type="button" className="icon-btn" aria-label={tr('compose.composeFooter.popOutReply')} data-tip={tr('compose.composeFooter.popOutReply')} onClick={onPopOut}>
          <ArrowUpRight />
        </button>
      )}
      <span className="status" aria-live="polite">
        {saveState === 'saving' ? tr('common.saving') : saveState === 'saved' ? tr('compose.composeFooter.draftSaved') : saveState === 'error' ? <span title={saveError}>{tr('compose.composeFooter.draftNotSaved', { value: saveError ? ` — ${saveError}` : '' })}</span> : ''}
      </span>
      <span className="spacer" />
      <button type="button" className="icon-btn" aria-label={tr('compose.composeFooter.discardDraft')} data-tip={tr('compose.composeFooter.discardDraft')} onClick={onDiscard}>
        <Trash2 />
      </button>
    </div>
  )
}

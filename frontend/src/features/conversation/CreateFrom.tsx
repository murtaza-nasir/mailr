// "Create from email": one menu for everything you make out of a conversation, in the
// reader's toolbar (Shift+T), in its ⋮ at phone width, and in the row menu. Task and Event open the
// same editors as before (FromMessageDialog, from the newest message); Meeting with these people
// opens the meeting step (ScheduleMeetingPicker); Note and Checklist make a note linked to the
// conversation; Link to a note or table opens the note picker.
// ACT mail.to_task @web.reader @web.list, ACT mail.to_event @web.reader @web.list, ACT mail.schedule_meeting @web.list
// ACT note.from_email @web.reader @web.list, ACT note.link @web.reader @web.list, ACT table.link @web.reader @web.list

import { useQueryClient } from '@tanstack/react-query'
import { CalendarClock, CalendarPlus, Link2, ListChecks, ListTodo, StickyNote } from 'lucide-react'
import { useNavigate } from 'react-router'
import { useConversation } from '../../api/hooks'
import type { Message } from '../../api/types'
import { MenuItem, MenuSep } from '../../components/Popover'
import { notePath } from '../../lib/routes'
import { toast } from '../../stores/ui'
import { noteFromEmail } from '../notes/actions'
import { runUndo } from '../notes/api'
import { refreshLinked } from '../notes/LinkedPanel'
import { t } from '../../i18n'

export interface CreateFromHandlers {
  /** Task or Event: the editor, from the newest message. */
  onFromMessage: (kind: 'task' | 'event', m: Message) => void
  /** Meeting with these people: the meeting step. */
  onMeeting: () => void
  /** Link to a note or table: the picker (the menu has closed by then). */
  onLink: () => void
}

export function CreateFromChoices({ convId, online = true, onClose, onFromMessage, onMeeting, onLink }: CreateFromHandlers & {
  convId: number
  online?: boolean
  onClose: () => void
}) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const det = useConversation(convId)
  const messages = det.data?.messages ?? []
  const last = [...messages].reverse().find((m) => !m.draft) ?? null
  const fromEmail = async (kind: 'text' | 'checklist') => {
    // Read before the menu closes: the text selected in a message goes into the note.
    const sel = window.getSelection()?.toString().trim() || null
    onClose()
    try {
      const { note, undo } = await noteFromEmail(qc, convId, sel, kind)
      refreshLinked(qc)
      const undoChoice = undo ? { secondaryLabel: t('common.undo'), onSecondary: () => void runUndo(qc, undo).then(() => refreshLinked(qc)) } : {}
      if (kind === 'text') {
        // A note opens at once, to be written in.
        nav(notePath(note.id))
        toast({ message: t('conversation.createFrom.noteMadeFromTheEmail'), ...undoChoice, duration: 7000 })
        return
      }
      toast({ message: t('conversation.createFrom.checklistMadeFromTheEmail'), actionLabel: t('common.open'), onAction: () => nav(notePath(note.id)), ...undoChoice, duration: 7000 })
    } catch {
      /* write() showed the error */
    }
  }
  return (
    <>
      <MenuItem icon={<ListTodo />} disabled={!last} onSelect={() => { if (!last) return; onClose(); onFromMessage('task', last) }}>
        {t('common.task')}
      </MenuItem>
      <MenuItem icon={<CalendarPlus />} disabled={!last} onSelect={() => { if (!last) return; onClose(); onFromMessage('event', last) }}>
        {t('common.event')}
      </MenuItem>
      <MenuItem icon={<CalendarClock />} disabled={!messages.length} onSelect={() => { onClose(); onMeeting() }}>
        {t('conversation.createFrom.meetingWithThesePeople')}
      </MenuItem>
      <MenuSep />
      <MenuItem icon={<StickyNote />} disabled={!online} onSelect={() => void fromEmail('text')}>
        {t('common.note')}
      </MenuItem>
      <MenuItem icon={<ListChecks />} disabled={!online} onSelect={() => void fromEmail('checklist')}>
        {t('conversation.createFrom.checklist')}
      </MenuItem>
      <MenuItem icon={<Link2 />} disabled={!online} onSelect={() => { onClose(); onLink() }}>
        {t('conversation.createFrom.linkToANoteOr')}
      </MenuItem>
    </>
  )
}

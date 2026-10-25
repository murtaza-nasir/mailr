// "Create from note": Task, Event and Email from a note, in the note editor's toolbar.
// A task or an event opens its editor with the note's title and text, and once saved it is linked to
// the note (note.link). An email opens the composer with the note as its text.
// ACT note.to_task @web.note, ACT note.to_event @web.note, ACT note.to_email @web.note

import { useQueryClient } from '@tanstack/react-query'
import { CalendarPlus, ListTodo, Mail } from 'lucide-react'
import { useState } from 'react'
import { MenuItem, Popover } from '../../components/Popover'
import { useCompose, emptyPayload } from '../../stores/compose'
import { MailEventEditor } from '../conversation/ScheduleMeeting'
import { nextHalfHour } from '../calendar/QuickCreate'
import { TaskEditor } from '../rail/QuickSheet'
import { linkNote } from './actions'
import { noteText } from './model'
import type { Note } from './types'
import { escapeHtml } from '../../lib/html'
import { t } from '../../i18n'

type Open = 'task' | 'event' | null

export function CreateFromNote({ note, anchor, open, onClose }: { note: Note; anchor: HTMLElement | DOMRect | null; open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [editor, setEditor] = useState<Open>(null)
  const title = note.title || (note.kind === 'checklist' ? t('common.untitledList') : t('common.untitledNote'))
  const text = noteText(note)
  const link = typeof window !== 'undefined' ? `${window.location.origin}/notes/n/${encodeURIComponent(note.id)}` : ''
  const withLink = [text, t('notes.createFromNote.fromTheNote', { link })].filter(Boolean).join('\n\n')
  // A task's notes are Markdown: the note's title is the link there (an event's description is plain text).
  const withTitleLink = [text, t('notes.createFromNote.fromTheNoteTitled', { title: title.replace(/([\\[\]])/g, '\\$1'), link })].filter(Boolean).join('\n\n')
  const pick = (what: 'task' | 'event' | 'email') => {
    onClose()
    if (what === 'email') {
      // The note's text, one paragraph per line; the address is left for you.
      const html = text.split('\n').map((l) => `<p>${escapeHtml(l) || '<br>'}</p>`).join('')
      useCompose.getState().open({ payload: { ...emptyPayload(), subject: note.title, html } })
      return
    }
    setEditor(what)
  }
  const start = nextHalfHour(new Date())
  return (
    <>
      <Popover anchor={anchor} open={open} onClose={onClose} placement="top-start" label={t('notes.createFromNote.createFromNote')} minWidth={200}>
        <MenuItem icon={<ListTodo size={16} />} onSelect={() => pick('task')}>{t('common.task')}</MenuItem>
        <MenuItem icon={<CalendarPlus size={16} />} onSelect={() => pick('event')}>{t('common.event')}</MenuItem>
        <MenuItem icon={<Mail size={16} />} onSelect={() => pick('email')}>{t('common.email')}</MenuItem>
      </Popover>
      {editor === 'task' && (
        <TaskEditor task={null} onClose={() => setEditor(null)} heading={t('notes.createFromNote.taskFromThisNote')} saveLabel={t('common.addTask')} createdLabel={t('notes.createFromNote.taskMadeFromTheNote')}
          seed={{ summary: title, notes: withTitleLink }}
          onCreated={(uid) => void linkNote(qc, note.id, 'task', { uid }).catch(() => undefined)} />
      )}
      {editor === 'event' && (
        <MailEventEditor init={{ mode: 'new', seed: { summary: title, start, end: new Date(start.getTime() + 3600_000) } }} description={withLink}
          onSaved={(ev) => void linkNote(qc, note.id, 'event', { uid: ev.uid }).catch(() => undefined)}
          onClose={() => setEditor(null)} />
      )}
    </>
  )
}

// "Save to note" on an assistant answer (note.from_answer): a text note with the
// answer's text and its sources, linked to the emails, events, tasks and recordings it cites. Web
// pages stay as lines under "Sources" (a note links only to things in mailr).

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import type { QueryClient } from '@tanstack/react-query'
import { request } from '../../api/client'
import { appNavigate } from '../../lib/appNav'
import { notePath } from '../../lib/routes'
import { toast, toastError } from '../../stores/ui'
import type { Citation } from '../rail/assistant/citations'
import { copyText } from '../rail/assistant/timeline'
import { createNote } from './actions'
import type { NoteLink } from './types'
import { t } from '../../i18n'

const BODY_MAX = 20_000

export interface AnswerNote {
  title: string
  body: string
  links: { target_kind: NoteLink['target_kind']; target_ref: Record<string, unknown> }[]
}

/** The first line of the answer as plain words, for the note's title. */
export function answerTitle(md: string): string {
  const line = md.split('\n').map((l) => l.trim()).find((l) => l && !/^[-=*_#>\s]+$/.test(l)) ?? ''
  const plain = line
    .replace(/\[(\d+)\]\(mailr-cite:\d+\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#+\s*|^[-*>]\s+|[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/ ([.,;:!?)])/g, '$1') // a citation removed before a full stop leaves no space behind
    .trim()
  return plain.length > 80 ? `${plain.slice(0, 79).trimEnd()}…` : plain
}

/** The note an answer becomes: its text with the sources it uses, and a link per cited record. */
export function answerNote(md: string, citations: Citation[]): AnswerNote {
  const body = copyText(md, citations).slice(0, BODY_MAX)
  const used = new Set([...md.matchAll(/\[(\d+)\]\(mailr-cite:\d+\)/g)].map((m) => Number(m[1])))
  const seen = new Set<string>()
  const links: AnswerNote['links'] = []
  const add = (kind: NoteLink['target_kind'], ref: Record<string, unknown>, key: string) => {
    if (seen.has(key)) return
    seen.add(key)
    links.push({ target_kind: kind, target_ref: ref })
  }
  for (const c of citations.filter((x) => used.has(x.n)).sort((a, b) => a.n - b.n)) {
    if ((c.kind === 'message' || c.kind === 'conversation') && c.conversation_id != null) add('conversation', { conversation_id: c.conversation_id }, `c:${c.conversation_id}`)
    else if (c.kind === 'event' && c.uid) add('event', { uid: c.uid, ...(c.occurrence ? { occurrence: c.occurrence } : {}) }, `e:${c.uid}`)
    else if (c.kind === 'task' && c.uid) add('task', { uid: c.uid }, `t:${c.uid}`)
    else if (c.kind === 'recording' && c.connector_id != null && c.recording_id != null) add('recording', { connector_id: c.connector_id, recording_id: c.recording_id }, `r:${c.connector_id}:${c.recording_id}`)
  }
  return { title: answerTitle(md) || t('notes.fromAnswer.from', { name: assistantNameNow() }), body, links }
}

/** Make the note and its links; the toast opens it and offers Undo (to Trash).
 *  ACT note.from_answer */
export async function saveAnswerToNote(qc: QueryClient, md: string, citations: Citation[]): Promise<string | null> {
  const n = answerNote(md, citations)
  try {
    const note = await createNote(qc, { kind: 'text', title: n.title, body: n.body }, { quiet: true })
    let failed = 0
    for (const l of n.links) {
      try {
        await request(`/notes/${encodeURIComponent(note.id)}/links`, { method: 'POST', body: l as never })
      } catch {
        failed += 1 // a record the note may not link to (gone, or someone else's): the text keeps it
      }
    }
    void qc.invalidateQueries({ queryKey: ['notes'] })
    toast({
      message: failed ? t('notes.fromAnswer.savedToANoteLinks', { count: failed }) : t('notes.fromAnswer.savedToANote'),
      actionLabel: t('common.open'), onAction: () => appNavigate(notePath(note.id)), duration: 6000,
    })
    return note.id
  } catch (e) {
    toastError(e, t('notes.fromAnswer.couldNotSaveTheAnswer'))
    return null
  }
}

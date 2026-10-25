// One handler for the verbs of a card, the editor, the keys and the ribbon, so a menu entry, a hover
// button and a key always do the same thing.

import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import { useNavigate } from 'react-router'
import { request } from '../../api/client'
import { notePath } from '../../lib/routes'
import { convertNote, duplicateNote, patchNote, restoreNote, setState, trashNote } from './actions'
import { localOnlyLabel } from './localOnly'
import { noteKeys } from './api'
import type { NoteVerb } from './menus'
import { downloadText, noteMarkdown } from './model'
import { leaveShared } from './sharing'
import { useNotesUI } from './store'
import type { Note } from './types'
import { t } from '../../i18n'

export function fileName(n: Pick<Note, 'title' | 'id'>): string {
  return `${(n.title || t('common.note')).replace(/[\\/:*?"<>|]+/g, ' ').trim().slice(0, 80) || t('common.note')}.md`
}

/** The Markdown of one note: the server's export, else the copy made here from what the app holds. */
export async function exportNote(n: Note): Promise<void> {
  // ACT note.export @web.note
  try {
    const text = await request<string>(`/notes/${encodeURIComponent(n.id)}/export`)
    downloadText(fileName(n), typeof text === 'string' ? text : noteMarkdown(n))
  } catch {
    downloadText(fileName(n), noteMarkdown(n))
  }
}

export function useNoteVerbs() {
  const qc = useQueryClient()
  const nav = useNavigate()
  return useCallback(async (n: Note, v: NoteVerb, anchor?: HTMLElement | DOMRect | null) => {
    switch (v) {
      case 'open':
        nav(notePath(n.id))
        break
      // ACT note.pin @web.notes @web.note
      case 'pin':
        await setState(qc, n.id, { pinned: !n.state.pinned }, n.state.pinned ? t('common.unpinned') : t('common.pinned'))
        break
      // ACT note.archive @web.notes @web.note
      case 'archive':
        await setState(qc, n.id, { archived: !n.state.archived }, n.state.archived ? t('common.unarchived') : t('common.archived'))
        break
      case 'labels':
        if (anchor) useNotesUI.getState().set({ labelsFor: { ids: [n.id], anchor } })
        break
      case 'duplicate':
        await duplicateNote(qc, n.id)
        break
      case 'copy_list':
      case 'copy_text': {
        const copy = await duplicateNote(qc, n.id)
        await convertNote(qc, copy.id, v === 'copy_list' ? 'checklist' : 'text')
        break
      }
      case 'history':
        useNotesUI.getState().set({ history: true })
        nav(notePath(n.id))
        break
      case 'export': {
        const full = qc.getQueryData<Note>(noteKeys.note(n.id)) ?? (await request<Note>(`/notes/${encodeURIComponent(n.id)}`))
        await exportNote(full)
        break
      }
      case 'delete':
        await trashNote(qc, n.id)
        break
      // ACT note.restore @web.notes @web.note
      case 'restore':
        await restoreNote(qc, n.id)
        break
      case 'purge':
        useNotesUI.getState().set({ purge: [n.id] })
        break
      case 'share':
        useNotesUI.getState().set({ share: { kind: 'note', id: n.id, name: n.title || (n.kind === 'checklist' ? t('common.untitledList') : t('common.untitledNote')) } })
        break
      case 'leave':
        await leaveShared(qc, 'note', n.id)
        break
      case 'local_only':
        await patchNote(qc, n.id, { local_only: !n.local_only }, { label: localOnlyLabel(!n.local_only) }).catch(() => undefined)
        break
      case 'repeat':
        useNotesUI.getState().set({ repeatFor: n })
        break
      case 'save_template':
        useNotesUI.getState().set({ saveTemplate: { kind: 'note', id: n.id, name: n.title || (n.kind === 'checklist' ? t('common.untitledList') : t('common.untitledNote')) } })
        break
    }
  }, [qc, nav])
}

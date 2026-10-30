// Rename a chat (PATCH /agent/sessions/{id}), from a row of Chats or from the title in the panel's
// header. The new title shows at once in the list, any search results and the header; a toast says so,
// with Undo, which renames it back (to the automatic title when it had that). A refused rename puts the
// old title back and says so. The logic of what to send is in chatList.ts.

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import { api } from '../../../api/client'
import { toast } from '../../../stores/ui'
import { t as tr } from '../../../i18n'
import { CHATS_KEY, renameBody, undoBody, withTitle, type ChatSummary, type RenameResult } from './chatList'

/** `applied(id, title, renamed)` runs whenever a chat's title changes here (the header shows it). */
export function useRenameChat(applied?: (id: string, title: string, renamed: boolean) => void) {
  const qc = useQueryClient()
  const appliedRef = useRef(applied)
  useEffect(() => {
    appliedRef.current = applied
  })

  const apply = useCallback(
    (id: string, title: string, renamed: boolean) => {
      // A list request already under way (a search cleared just before) carries the title from before
      // the rename; landing after this, it would put that title back. Cancel it first.
      void qc.cancelQueries({ queryKey: CHATS_KEY })
      // The list and every search the cache holds.
      qc.setQueriesData<ChatSummary[]>({ queryKey: CHATS_KEY }, (list) => withTitle(list, id, title, renamed))
      appliedRef.current?.(id, title, renamed)
    },
    [qc],
  )
  /** The server has the title: the lists are loaded again, from after the change. */
  const settled = useCallback(
    (id: string, title: string, renamed: boolean) => {
      apply(id, title, renamed)
      void qc.invalidateQueries({ queryKey: CHATS_KEY })
    },
    [apply, qc],
  )

  const send = useCallback(
    (id: string, body: { title: string }) => api.patch<RenameResult>(`/agent/sessions/${encodeURIComponent(id)}`, body),
    [],
  )

  /** ACT agent.chat_rename @web.panel: rename `chat` to what was typed. False when nothing was sent. */
  return useCallback(
    (chat: Pick<ChatSummary, 'id' | 'title' | 'renamed'>, typed: string): boolean => {
      const body = renameBody(typed, chat)
      if (!body) return false
      const before = { title: chat.title, renamed: Boolean(chat.renamed) }
      // An empty title waits for the server, which knows the automatic one.
      if (body.title) apply(chat.id, body.title, true)
      void send(chat.id, body).then(
        (r) => {
          settled(chat.id, r.title, r.renamed)
          toast({
            message: tr('rail.assistant.chatList.renamed'),
            actionLabel: tr('common.undo'),
            onAction: () => {
              apply(chat.id, r.previous.title, r.previous.renamed)
              void send(chat.id, undoBody(r.previous)).then(
                (u) => settled(chat.id, u.title, u.renamed),
                () => {
                  apply(chat.id, r.title, r.renamed)
                  toast({ message: tr('rail.assistant.chatList.renameFailed'), error: true })
                },
              )
            },
          })
        },
        () => {
          apply(chat.id, before.title, before.renamed)
          toast({ message: tr('rail.assistant.chatList.renameFailed'), error: true })
        },
      )
      return true
    },
    [apply, settled, send],
  )
}

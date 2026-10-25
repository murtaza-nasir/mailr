// Where a link chip opens: a row's form over its table, an email, an event, a task, a note or a table.

import { eventPath, notePath, tableRoutePath, taskPath } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { refKey } from './model'
import type { LinkRef, RefInfo } from './types'

export function refPath(r: LinkRef): string {
  switch (r.kind) {
    case 'row': return tableRoutePath(r.table, null, r.row)
    case 'conversation': return mailPath('inbox', Number(r.conversation_id))
    case 'event': return eventPath(r.uid, r.occurrence ?? '')
    case 'task': return taskPath(r.uid)
    default: return r.id.startsWith('t') ? tableRoutePath(r.id) : notePath(r.id)
  }
}

export function openRef(nav: (to: string) => void, r: LinkRef, refs: Record<string, RefInfo>): void {
  if (refs[refKey(r)]?.state !== 'ok') return
  nav(refPath(r))
}

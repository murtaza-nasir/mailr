import { useEffect, type MutableRefObject } from 'react'
import type { Editor as TiptapEditor } from '@tiptap/react'
import { api } from '../../api/client'
import { useApiList } from '../../api/hooks'
import type { Address, Template } from '../../api/types'
import { shortcutBefore } from '../../lib/templates'
import { toastError } from '../../stores/ui'
import { t as tr } from '../../i18n'

interface Opts {
  editor: TiptapEditor | null
  to: Address[]
  cc: Address[]
  bcc: Address[]
  identityId: number | null
  subject: string
  setSubject: (s: string) => void
  touch: () => void
  /** The editor's key handler for a typed `;shortcut` (Space or Tab after it inserts the template). */
  keyRef: MutableRefObject<(ed: TiptapEditor, e: KeyboardEvent) => boolean>
}

/** Templates in the compose form: the list for the menu, and inserting one. */
export function useComposeTemplates({ editor, to, cc, bcc, identityId, subject, setSubject, touch, keyRef }: Opts) {
  const templates = useApiList<Template>(['templates'], '/templates')

  // ACT template.insert @web.editor: the server fills the variables for the current recipients and sending
  // address; the text goes in at the cursor (or in place of the typed `;shortcut`), and the
  // subject is set only when the draft has none.
  const insertTemplate = async (t: Template, range?: { from: number; to: number }) => {
    if (!editor) return
    try {
      const r = await api.post<{ subject: string; body_html: string }>(`/templates/${t.id}/expand`, {
        to, cc, bcc, identity_id: identityId,
      } as unknown as Record<string, unknown>)
      const chain = editor.chain().focus()
      ;(range ? chain.insertContentAt(range, r.body_html) : chain.insertContent(r.body_html)).run()
      if (r.subject && !subject.trim()) setSubject(r.subject)
      touch()
    } catch (e) {
      toastError(e, tr('compose.useComposeTemplates.templateNotInserted'))
    }
  }
  useEffect(() => {
    keyRef.current = (ed, e) => {
      if ((e.key !== ' ' && e.key !== 'Tab') || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey || e.isComposing) return false
      const sel = ed.state.selection
      if (!sel.empty) return false
      const { $from } = sel
      const name = shortcutBefore($from.parent.textBetween(0, $from.parentOffset, undefined, '￼'))
      const t = name ? (templates.data ?? []).find((x) => x.shortcut === name) : undefined
      if (!t) return false
      e.preventDefault()
      void insertTemplate(t, { from: $from.pos - t.shortcut.length - 1, to: $from.pos })
      return true
    }
  })
  return { templates: templates.data, insertTemplate }
}

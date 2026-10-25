// Templates (`note.save_template`, `note.from_template`): the gallery behind
// "New from template" in the capture bar, and the dialog that saves a note, a list or a table as a
// template. A template is a text note, a checklist (its sections and items, all unchecked) or a
// table (its columns). The built-in starters hold no personal data and cannot be deleted.

import { useQueryClient } from '@tanstack/react-query'
import { ListChecks, StickyNote, Table2, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Skeleton } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { deleteTemplate, fromTemplate, saveTableTemplate, saveTemplate } from './actions'
import { useTemplates } from './api'
import type { NoteTemplate } from './types'
import { t as tr } from '../../i18n'

const KIND_ICON: Record<NoteTemplate['kind'], ReactNode> = {
  text: <StickyNote size={18} aria-hidden />, checklist: <ListChecks size={18} aria-hidden />, table: <Table2 size={18} aria-hidden />,
}
const KIND_NAME: Record<NoteTemplate['kind'], string> = { text: tr('common.note'), checklist: tr('common.list'), table: tr('common.table') }

/** The lines a template's card previews: a list's sections, a table's columns, a note's first lines. */
export function templatePreview(t: Pick<NoteTemplate, 'kind' | 'content'>, n = 4): string[] {
  const c = t.content as { body?: string; sections?: { name: string }[]; items?: { text: string }[]; columns?: { name: string }[] }
  if (t.kind === 'table') return (c.columns ?? []).map((x) => x.name).slice(0, n)
  if (t.kind === 'checklist') {
    const secs = (c.sections ?? []).map((x) => x.name)
    return (secs.length ? secs : (c.items ?? []).map((x) => x.text)).slice(0, n)
  }
  // An empty bullet ("- ") is a blank to fill in: the preview leaves it out.
  return (c.body ?? '').split('\n').map((l) => l.trim()).filter((l) => l && l !== '-').slice(0, n)
}

/** "New from template": the built-in starters, then the user's own. A click makes the note, list or
 *  table and opens it; the toast offers Undo. The user's own have a Delete button (with Undo).
 *  ACT note.from_template @web.notes, ACT note.delete_template @web.notes */
export function TemplatesDialog({ label, onClose, onMade }: {
  /** The label view the gallery opened from: the new note gets it. */
  label: string | null
  onClose: () => void
  onMade: (made: { note?: { id: string }; table?: { id: string } }) => void
}) {
  const qc = useQueryClient()
  const q = useTemplates()
  const [busy, setBusy] = useState(false)
  const all = q.data ?? []
  const groups: [string, NoteTemplate[]][] = [[tr('notes.templates.starters'), all.filter((t) => t.builtin)], [tr('notes.templates.yourTemplates'), all.filter((t) => !t.builtin)]]
  const use = async (t: NoteTemplate) => {
    setBusy(true)
    try {
      const made = await fromTemplate(qc, t.id, label ? { labels: [label] } : {})
      onClose()
      onMade(made)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog open onClose={onClose} title={tr('notes.templates.newFromTemplate')} footer={<button type="button" className="btn text" onClick={onClose}>{tr('common.close')}</button>}>
      <div className="nt-templates" data-testid="templates-dialog">
        {q.isLoading ? <Skeleton height={120} /> : groups.map(([name, list]) => (
          <section key={name} aria-label={name}>
            <h3 className="nt-templates-head">{name}</h3>
            {!list.length ? (
              <p className="muted nt-small">{tr('notes.templates.saveANoteAList')}</p>
            ) : (
              <ul className="nt-template-grid">
                {list.map((t) => (
                  <li key={t.id} className="nt-template" data-template={t.id}>
                    <button type="button" className="nt-template-use" disabled={busy} onClick={() => void use(t)} aria-label={tr('notes.templates.newFrom', { kindName: KIND_NAME[t.kind].toLowerCase(), itemName: t.name })}>
                      <span className="nt-template-title">{KIND_ICON[t.kind]} {t.name}</span>
                      <span className="muted nt-small">{KIND_NAME[t.kind]} · {t.summary}</span>
                      <span className="nt-template-lines">{templatePreview(t).map((l, i) => <span key={i}>{l}</span>)}</span>
                    </button>
                    {!t.builtin && (
                      <button type="button" className="icon-btn sm nt-template-del" aria-label={tr('notes.templates.deleteTheTemplate', { itemName: t.name })} data-tip={tr('notes.templates.deleteTemplate')}
                        onClick={() => void deleteTemplate(qc, t.id, t.name)}><Trash2 size={16} /></button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        ))}
      </div>
    </Dialog>
  )
}

/** Save a note, a list or a table as a template: a name (the title by default).
 *  ACT note.save_template @web.note @web.notes, ACT table.save_template @web.table */
export function SaveTemplateDialog({ kind, id, name, onClose }: { kind: 'note' | 'table'; id: string; name: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [v, setV] = useState(name)
  const save = async () => {
    const n = v.trim() || name || tr('notes.templates.untitledTemplate')
    onClose()
    await (kind === 'table' ? saveTableTemplate(qc, id, n) : saveTemplate(qc, id, n))
  }
  return (
    <Dialog open onClose={onClose} title={tr('common.saveAsTemplate')} footer={
      <>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" onClick={() => void save()}>{tr('notes.templates.saveTemplate')}</button>
      </>
    }>
      <form onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}>
        <label className="field">
          <span>{tr('notes.templates.templateName')}</span>
          <input value={v} maxLength={200} data-autofocus autoFocus onChange={(e) => setV(e.target.value)} />
        </label>
        <p className="muted nt-small">
          {kind === 'table' ? tr('notes.templates.theTemplateKeepsTheColumns')
            : tr('notes.templates.theTemplateKeepsTheTitle')}
        </p>
      </form>
    </Dialog>
  )
}

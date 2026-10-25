import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useApiList } from '../../api/hooks'
import type { Template } from '../../api/types'
import { Skeleton, LoadFailed, failedEmpty } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { EditorArea, FormatBar, useMailEditor } from '../compose/Editor'
import { Section } from './common'
import { t as tr, tx } from '../../i18n'

const VARIABLES = '{first_name}, {name}, {email}, {my_name}, {date}'

export function TemplatesTab() {
  const qc = useQueryClient()
  const q = useApiList<Template>(['templates'], '/templates')
  const [editing, setEditing] = useState<Partial<Template> | null>(null)
  const [deleting, setDeleting] = useState<Template | null>(null)
  const items = q.data ?? []
  return (
    <Section
      title={tr('common.templates')}
      desc={
        <>
          {tx('settings.templatesTab.textYouUseOftenIn', { variables: VARIABLES }, { code: (c) => <code>{c}</code> })}
        </>
      }
      actions={
        <button type="button" className="btn tonal" onClick={() => setEditing({ name: '', shortcut: '', subject: '', body_html: '' })}>
          <Plus />{' '}{tr('settings.templatesTab.newTemplate')}
        </button>
      }
    >
      {q.isLoading ? (
        <Skeleton height={60} />
      ) : failedEmpty(q) ? (
        <LoadFailed what="templates" q={q} />
      ) : items.length === 0 ? (
        <p className="muted">{tr('settings.templatesTab.noTemplatesYet')}</p>
      ) : (
        <div className="table-wrap"><table className="table" aria-label={tr('common.templates')}>
          <tbody>
            {items.map((t) => (
              <tr key={t.id}>
                <td>
                  <b>{t.name}</b>
                  {t.subject && <div className="muted">{tr('settings.templatesTab.subject', { subject: t.subject })}</div>}
                </td>
                <td style={{ width: 160 }}>{t.shortcut ? <code>;{t.shortcut}</code> : <span className="muted">{tr('settings.templatesTab.noShortcut')}</span>}</td>
                <td style={{ width: 150 }}>
                  <button type="button" className="btn text sm" aria-label={tr('settings.templatesTab.edit', { itemName: t.name })} onClick={() => setEditing(t)}>
                    {tr('common.edit')}
                  </button>
                  <button type="button" className="btn text sm" aria-label={tr('settings.templatesTab.delete', { itemName: t.name })} onClick={() => setDeleting(t)}>
                    {tr('common.delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {editing && <TemplateDialog template={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        title={tr('settings.templatesTab.deleteTemplate')}
        body={tr('settings.templatesTab.deleteTheTemplateMessagesThat', { deletingName: deleting?.name })}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const d = deleting
          setDeleting(null)
          // ACT template.delete @web.settings
          if (d)
            api
              .del(`/templates/${d.id}`)
              .then(() => {
                void qc.invalidateQueries({ queryKey: ['templates'] })
                toast({ message: tr('settings.templatesTab.templateDeleted') })
              })
              .catch((e) => toastError(e, tr('settings.templatesTab.couldNotDelete')))
        }}
      />
    </Section>
  )
}

function TemplateDialog({ template, onClose }: { template: Partial<Template>; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(template.name ?? '')
  const [shortcut, setShortcut] = useState(template.shortcut ?? '')
  const [subject, setSubject] = useState(template.subject ?? '')
  const [body, setBody] = useState(template.body_html ?? '')
  const editor = useMailEditor({ html: template.body_html ?? '', onChange: setBody, placeholder: tr('settings.templatesTab.templateText') })
  const badShortcut = shortcut.trim() !== '' && !/^;?[a-z0-9][a-z0-9_-]{0,47}$/i.test(shortcut.trim())
  // ACT template.create @web.settings, ACT template.edit @web.settings
  const save = useMutation({
    mutationFn: () => {
      const data = { name: name.trim(), shortcut: shortcut.trim(), subject: subject.trim(), body_html: body }
      return template.id ? api.patch(`/templates/${template.id}`, data) : api.post('/templates', data)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['templates'] })
      toast({ message: tr('settings.templatesTab.templateSaved') })
      onClose()
    },
    onError: (e) => toastError(e, tr('settings.templatesTab.couldNotSave')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={template.id ? tr('settings.templatesTab.editTemplate') : tr('settings.templatesTab.newTemplate')}
      wide
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!name.trim() || badShortcut || save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <div className="field">
            <label htmlFor="tpl-name">{tr('common.name')}</label>
            <input id="tpl-name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="tpl-shortcut">{tr('settings.templatesTab.shortcutOptional')}</label>
            <input id="tpl-shortcut" className="input" value={shortcut} onChange={(e) => setShortcut(e.target.value)} placeholder="thanks" aria-invalid={badShortcut} />
            {badShortcut && <span style={{ color: 'var(--danger)', fontSize: 12 }}>{tr('settings.templatesTab.lettersDigitsAndOnly')}</span>}
          </div>
        </div>
        <div className="field">
          <label htmlFor="tpl-subject">{tr('settings.templatesTab.subjectOptionalUsedWhenThe')}</label>
          <input id="tpl-subject" className="input" value={subject} onChange={(e) => setSubject(e.target.value)} />
        </div>
        <div className="field">
          <span className="field-label">{tr('common.text')}</span>
          <div className="card editor-card" style={{ minHeight: 200 }}>
            <FormatBar editor={editor} />
            <EditorArea editor={editor} />
          </div>
          <span className="muted" style={{ fontSize: 12.5 }}>{tr('settings.templatesTab.variables', { variables: VARIABLES })}</span>
        </div>
      </div>
    </Dialog>
  )
}

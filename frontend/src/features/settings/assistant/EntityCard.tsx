// The card of a topic (an organization, project, topic or place) or of a person Ambrose knows who is
// not a contact: name, kind, aliases, addresses or domains, a one-line summary, and its memories.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Skeleton } from '../../../components/bits'
import { ConfirmDialog, Dialog } from '../../../components/Dialog'
import { toast, toastError } from '../../../stores/ui'
import { assistantNameNow } from '../../rail/assistant/useAssistantName'
import { MemoryDialog, MemoryRow, invalidateKnowledge } from './memoryBits'
import { ENTITY_KINDS, TOPIC_KINDS, qkA, type Entity, type EntityDetail, type EntityKind, type Memory } from './types'
import { t as tr, appLocale } from '../../../i18n'

const list = (s: string) =>
  s
    .split(/[,;\n]/)
    .map((x) => x.trim())
    .filter(Boolean)

/** A topic's card (name, aliases, domains, memories), or a person Ambrose knows who is not a
 *  contact. Also adds one. ACT memory.topic_edit @web.settings */
export function EntityDialog({ id, initialKind, onClose, onCreated }: {
  id: number | null; initialKind: EntityKind; onClose: () => void; onCreated?: (e: Entity) => void
}) {
  const qc = useQueryClient()
  const detail = useQuery<EntityDetail>({ queryKey: qkA.entity(id ?? 0), queryFn: () => api.get(`/assistant/entities/${id}`), enabled: id !== null })
  const d = detail.data
  const [form, setForm] = useState<{ kind: EntityKind; name: string; aliases: string; emails: string; domains: string; summary: string } | null>(
    id === null ? { kind: initialKind, name: '', aliases: '', emails: '', domains: '', summary: '' } : null,
  )
  useEffect(() => {
    if (d && !form) setForm({ kind: d.kind, name: d.name, aliases: d.aliases.join(', '), emails: d.emails.join(', '), domains: d.domains.join(', '), summary: d.summary })
  }, [d, form])
  const [memEdit, setMemEdit] = useState<Partial<Memory> | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)

  const save = useMutation({
    mutationFn: () => {
      const f = form!
      const body = { kind: f.kind, name: f.name.trim(), aliases: list(f.aliases), emails: list(f.emails).map((x) => x.toLowerCase()), domains: list(f.domains), summary: f.summary.trim() }
      return id === null ? api.post<{ entity: Entity }>('/assistant/entities', body) : api.patch<{ entity: Entity }>(`/assistant/entities/${id}`, body)
    },
    onSuccess: (res) => {
      invalidateKnowledge(qc)
      toast({ message: id === null ? tr('settings.assistant.peopleSection.added', { formName: form?.name }) : tr('common.saved2') })
      if (id === null && res?.entity) onCreated?.(res.entity)
      onClose()
    },
    onError: (e) => toastError(e, tr('common.notSaved')),
  })
  const del = useMutation({
    mutationFn: () => api.del(`/assistant/entities/${id}`),
    onSuccess: () => {
      invalidateKnowledge(qc)
      toast({ message: tr('settings.assistant.peopleSection.andItsMemoriesDeleted', { itemName: d?.name ?? tr('settings.assistant.peopleSection.entry') }) })
      onClose()
    },
    onError: (e) => toastError(e),
  })

  const set = (k: keyof NonNullable<typeof form>, v: string) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const badEmail = form ? list(form.emails).find((x) => !x.includes('@')) : undefined

  return (
    <Dialog
      open
      wide
      modal
      onClose={onClose}
      title={id === null ? tr('settings.assistant.peopleSection.addPersonOrThing') : d?.name ?? '…'}
      footer={
        <>
          {id !== null && (
            <button type="button" className="btn text as-danger-text" onClick={() => setConfirmDel(true)}>
              <Trash2 size={16} />{' '}{tr('common.delete2')}
            </button>
          )}
          <span className="spacer" />
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!form?.name.trim() || !!badEmail || save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      {!form ? (
        <Skeleton height={200} />
      ) : (
        <div className="stack as-dialog">
          <div className="grid-3 as-ent-form">
            <div className="field">
              <label htmlFor="en-name">{tr('common.name')}</label>
              <input id="en-name" className="input" value={form.name} placeholder={tr('settings.assistant.peopleSection.samAdams')} onChange={(e) => set('name', e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="en-kind">{tr('common.kind')}</label>
              <select id="en-kind" className="select" value={form.kind} onChange={(e) => set('kind', e.target.value)}>
                {(id === null ? ENTITY_KINDS : form.kind === 'person' ? ENTITY_KINDS.filter((k) => k.id === 'person') : TOPIC_KINDS).map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="en-alias">{tr('settings.assistant.peopleSection.alsoCalled')}</label>
              <input id="en-alias" className="input" value={form.aliases} placeholder={tr('settings.assistant.peopleSection.bethanyB')} onChange={(e) => set('aliases', e.target.value)} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="en-sum">{tr('settings.assistant.peopleSection.whoOrWhatIsThis')}</label>
            <input id="en-sum" className="input" value={form.summary} maxLength={600} placeholder={tr('settings.assistant.peopleSection.myPartnerWorksAtAcme')} onChange={(e) => set('summary', e.target.value)} />
          </div>
          <div className="grid-2">
            <div className="field">
              <label htmlFor="en-emails">{tr('settings.assistant.peopleSection.emailAddresses')}</label>
              <input id="en-emails" className="input" value={form.emails} placeholder={tr('settings.assistant.peopleSection.samExampleComSamWork')} onChange={(e) => set('emails', e.target.value)} />
              {badEmail ? <span className="error">{tr('settings.assistant.peopleSection.isNotAnEmailAddress', { badEmail })}</span> : <span className="hint">{tr('settings.assistant.peopleSection.commaSeparatedLetsFindTheir', { name: assistantNameNow() })}</span>}
            </div>
            <div className="field">
              <label htmlFor="en-dom">{tr('settings.assistant.peopleSection.domains')}</label>
              <input id="en-dom" className="input" value={form.domains} placeholder="acme.example" onChange={(e) => set('domains', e.target.value)} />
              <span className="hint">{tr('settings.assistant.peopleSection.forOrganizationsMailFromThese')}</span>
            </div>
          </div>

          {id !== null && d && (
            <>
              <div className="row as-subhead">
                <h3 className="sub-h" style={{ flex: 1 }}>
                  {tr('settings.assistant.peopleSection.memories')}
                </h3>
                <button type="button" className="btn text sm" onClick={() => setMemEdit({})}>
                  <Plus size={16} />{' '}{tr('common.addMemory')}
                </button>
              </div>
              {d.memories.length === 0 ? (
                <p className="muted as-empty">{tr('settings.assistant.peopleSection.noMemoriesYet')}</p>
              ) : (
                <ul className="as-mems">
                  {d.memories.map((m) => (
                    <MemoryRow key={m.id} m={m} hideEntity onEdit={setMemEdit} />
                  ))}
                </ul>
              )}
              {d.contacts.length > 0 && (
                <>
                  <h3 className="sub-h">{tr('settings.assistant.peopleSection.inYourContacts')}</h3>
                  <ul className="as-contacts">
                    {d.contacts.map((c) => (
                      <li key={c.email}>
                        <span className="as-contact-main">
                          <span>{c.name || c.email}</span>
                          <span className="muted">
                            {c.email}
                            {c.organization ? ` · ${c.organization}` : ''}
                          </span>
                        </span>
                        <span className="muted as-contact-stats">
                          {tr('settings.assistant.peopleSection.fromTo', { receivedCount: c.received_count, sentCount: c.sent_count, value: c.last_seen ? tr('settings.assistant.peopleSection.last', { localeDateString: new Date(c.last_seen).toLocaleDateString(appLocale()) }) : '' })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </div>
      )}
      {memEdit && d && <MemoryDialog memory={memEdit} fixedEntity={d} onClose={() => { setMemEdit(null); void detail.refetch() }} />}
      <ConfirmDialog
        open={confirmDel}
        title={tr('settings.assistant.peopleSection.delete', { itemName: d?.name ?? '' })}
        body={tr('settings.assistant.peopleSection.thisDeletesAndMailAnd', { itemName: d?.name ?? 'it', value: d?.memories.length ? tr('settings.assistant.peopleSection.itsMemories', { count: d.memories.length }) : tr('settings.assistant.peopleSection.itsMemories2') })}
        confirmLabel={tr('common.delete2')}
        danger
        onClose={() => setConfirmDel(false)}
        onConfirm={() => {
          setConfirmDel(false)
          del.mutate()
        }}
      />
    </Dialog>
  )
}

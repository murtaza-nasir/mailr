import { assistantNameNow, withAssistantName } from '../../rail/assistant/useAssistantName'
// Shared pieces for memories: a row, the add/edit dialog, and mutations.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, ListChecks, Lock, MessageSquare, Pencil, Pin, PinOff, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useLiveForm } from '../../../lib/live/useLiveForm'
import { ConflictBar, EditingBanner } from '../../live/LiveBars'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { Dialog } from '../../../components/Dialog'
import { toast, toastError } from '../../../stores/ui'
import { Check } from '../common'
import { ENTITY_KINDS, MEMORY_KINDS, SOURCE_LABELS, qkA, type Entity, type Memory, type MemoryKind } from './types'
import { mailPath } from '../../../lib/views'
import { createTask } from '../../tasks/actions'
import { EntityDialog } from './EntityCard'
import { useAskAbout } from '../../assistant-context/askAbout'
import { t, appLocale } from '../../../i18n'
import '../../../styles/assistant-settings.css'

export function invalidateKnowledge(qc: ReturnType<typeof useQueryClient>) {
  void qc.invalidateQueries({ queryKey: ['assistant'] })
  // A person card shows the person's notes (What Ambrose knows).
  void qc.invalidateQueries({ queryKey: ['people', 'card'] })
}

export function kindLabel(k: string) {
  return MEMORY_KINDS.find((x) => x.id === k)?.label ?? k
}

export function kindExample(k: string) {
  return MEMORY_KINDS.find((x) => x.id === k)?.example ?? ''
}

/** A commitment as a task: "You promised Sam a quote" → "Promised Sam a quote". */
export function taskSummaryOf(text: string): string {
  const s = text.trim().replace(/^you\s+/i, '').replace(/\.$/, '')
  return s ? s[0].toUpperCase() + s.slice(1) : text
}

export function entityKindLabel(k: string) {
  return ENTITY_KINDS.find((x) => x.id === k)?.label ?? k
}

function fmtDate(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString(appLocale(), { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' })
}

export function conversationOf(m: Memory): number | null {
  return m.refs.find((r) => r.conversation_id)?.conversation_id ?? null
}

export function useMemoryMutations() {
  const qc = useQueryClient()
  const done = () => invalidateKnowledge(qc)
  const pin = useMutation({
    mutationFn: (m: Memory) => api.patch(`/assistant/memories/${m.id}`, { pinned: !m.pinned }),
    onSuccess: done,
    onError: (e) => toastError(e),
  })
  const remove = useMutation({
    mutationFn: (m: Memory) => api.del(`/assistant/memories/${m.id}`).then(() => m),
    onSuccess: (m) => {
      done()
      toast({
        message: t('settings.assistant.memoryBits.memoryDeleted'),
        actionLabel: t('common.undo'),
        onAction: () =>
          void api
            .post('/assistant/memories', { text: m.text, kind: m.kind, entity_id: m.entity_id, pinned: m.pinned, expires_at: m.expires_at, sensitive: m.sensitive })
            .then(done)
            .catch((e) => toastError(e)),
      })
    },
    onError: (e) => toastError(e),
  })
  // ACT memory.make_task @web.settings: a commitment becomes a task, linked to the email it came from.
  const task = useMutation({
    mutationFn: (m: Memory) => {
      const conv = conversationOf(m)
      return createTask(qc, { summary: taskSummaryOf(m.text), ...(conv ? { conversation_id: conv } : {}) })
    },
    onSuccess: (made) => {
      if (made) toast({ message: t('memory.page.taskMade') })
    },
  })
  return { pin, remove, task }
}

/** Where a memory came from. "From a chat" opens that chat at the message it came from, while the
 *  chat exists (the API's `chat`). */
// ACT memory.open_chat @web.settings
export function MemorySource({ m }: { m: Pick<Memory, 'source' | 'chat'> }) {
  const label = withAssistantName(SOURCE_LABELS[m.source])
  const chat = m.source === 'chat' ? m.chat : null
  if (!chat) return <span>{label}</span>
  return (
    <button type="button" className="as-link" data-tip={t('settings.assistant.memoryBits.openTheChat')}
      onClick={() => useAskAbout.getState().openChat(chat.session_id, chat.chat_message_id)}>
      <MessageSquare size={12} aria-hidden />{' '}{label}
    </button>
  )
}

export function MemoryRow({ m, onEdit, hideEntity }: { m: Memory; onEdit: (m: Memory) => void; hideEntity?: boolean }) {
  const { pin, remove, task } = useMemoryMutations()
  const conv = conversationOf(m)
  const expired = m.expires_at && new Date(m.expires_at) < new Date()
  return (
    <li className={`as-mem${m.pinned ? ' pinned' : ''}${expired ? ' expired' : ''}`}>
      <div className="as-mem-main">
        <div className="as-mem-text">{m.text}</div>
        <div className="as-mem-meta">
          <span className="pill" data-tip={kindExample(m.kind)}>{kindLabel(m.kind)}</span>
          {!hideEntity && m.entity && <span className="pill info">{m.entity.is_self ? t('memory.dialog.you') : m.entity.name}</span>}
          <MemorySource m={m} />
          {m.expires_at && <span>{expired ? t('common.expired') : t('settings.assistant.memoryBits.until')} {fmtDate(m.expires_at)}</span>}
          {m.sensitive && (
            <span className="as-flag" data-tip={t('settings.assistant.memoryBits.learnedFromALocalOnly')}>
              <Lock size={12} />{' '}{t('common.localOnly2')}
            </span>
          )}
          {m.updated_at && <span>{fmtDate(m.updated_at)}</span>}
          {conv && (
            <Link to={mailPath('all', conv)} className="as-link">
              <ExternalLink size={12} />{' '}{t('settings.assistant.memoryBits.source')}
            </Link>
          )}
        </div>
      </div>
      <div className="as-item-actions">
        {m.kind === 'commitment' && (
          <button type="button" className="icon-btn sm" aria-label={t('memory.page.makeTask')} data-tip={t('memory.page.makeTask')} disabled={task.isPending} onClick={() => task.mutate(m)}>
            <ListChecks size={18} />
          </button>
        )}
        <button type="button" className={`icon-btn sm${m.pinned ? ' active' : ''}`} aria-pressed={m.pinned} aria-label={m.pinned ? t('common.unpin') : t('common.pin')} data-tip={m.pinned ? t('common.unpin') : t('settings.assistant.memoryBits.pinAlwaysGiveThisTo', { name: assistantNameNow() })} onClick={() => pin.mutate(m)}>
          {m.pinned ? <PinOff size={18} /> : <Pin size={18} />}
        </button>
        <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.memoryBits.editMemory')} data-tip={t('common.edit2')} onClick={() => onEdit(m)}>
          <Pencil size={18} />
        </button>
        <button type="button" className="icon-btn sm" aria-label={t('settings.assistant.memoryBits.deleteMemory')} data-tip={t('common.delete2')} onClick={() => remove.mutate(m)}>
          <Trash2 size={18} />
        </button>
      </div>
    </li>
  )
}

export function useEntityOptions() {
  return useQuery<{ items: Entity[] }>({ queryKey: qkA.entities('', ''), queryFn: () => api.get('/assistant/entities') })
}

/** Add or edit a memory. `fixedEntity` pins it to one entity (the People & memories panel). */
interface MemForm extends Record<string, unknown> {
  text: string
  kind: MemoryKind
  /** An entity's id, or null for "You" (About you). */
  entityId: number | null
  pinned: boolean
  expires: string
}

/** `aboutPerson`: a contact Ambrose has no entry for yet (the person card); saving makes one. */
export function MemoryDialog({ memory, fixedEntity, aboutPerson, onClose }: {
  memory: Partial<Memory>; fixedEntity?: Entity; aboutPerson?: { name: string; email: string }; onClose: () => void
}) {
  const qc = useQueryClient()
  const ents = useEntityOptions().data?.items ?? []
  // One form object, so a change saved elsewhere can be merged in (lib/live/useLiveForm.ts).
  const [f, setF] = useState<MemForm>(() => ({
    text: memory.text ?? '', kind: memory.kind ?? 'fact',
    entityId: fixedEntity?.id ?? (memory.entity?.is_self ? null : memory.entity_id ?? null),
    pinned: memory.pinned ?? false, expires: memory.expires_at ? memory.expires_at.slice(0, 10) : '',
  }))
  const { text, kind, entityId, pinned, expires } = f
  const setText = (v: string) => setF((x) => ({ ...x, text: v }))
  const setKind = (v: MemoryKind) => setF((x) => ({ ...x, kind: v }))
  const setEntityId = (v: number | null) => setF((x) => ({ ...x, entityId: v }))
  const setPinned = (v: boolean) => setF((x) => ({ ...x, pinned: v }))
  const [adding, setAdding] = useState(false)
  const setExpires = (v: string) => setF((x) => ({ ...x, expires: v }))
  // ACT settings.memory_live_update @web.settings: the memory follows changes the assistant saves meanwhile.
  const live = useLiveForm<MemForm>({
    kind: 'memory', id: memory.id ?? null, form: f, setForm: setF, textFields: { text: 'lines' },
    fromContent: (c) => ({ text: String(c.text ?? ''), kind: (c.kind as MemoryKind) ?? 'fact', entityId: (c.entity_id as number | null) ?? null,
      pinned: !!c.pinned, expires: typeof c.expires_at === 'string' ? c.expires_at.slice(0, 10) : '' }),
    fields: (x) => ({ text: x.text, kind: x.kind }),
  })
  const save = useMutation({
    mutationFn: () => {
      // "You": the note goes under About you (the server finds or makes your own entry).
      const about = aboutPerson && !fixedEntity ? { entity_id: null, about: { name: aboutPerson.name || aboutPerson.email.split('@')[0], emails: [aboutPerson.email], kind: 'person' } }
        : entityId == null ? { entity_id: null, about: 'me' } : { entity_id: entityId }
      const body = { text: text.trim(), kind, ...about, pinned, expires_at: expires ? new Date(`${expires}T23:59:00`).toISOString() : null,
        ...(memory.id && live.baseVersion() != null ? { base_version: live.baseVersion() } : {}) }
      return memory.id ? api.patch(`/assistant/memories/${memory.id}`, body) : api.post('/assistant/memories', body)
    },
    onSuccess: () => {
      invalidateKnowledge(qc)
      toast({ message: memory.id ? t('settings.assistant.memoryBits.memorySaved') : t('settings.assistant.memoryBits.willRememberThat', { name: assistantNameNow() }) })
      onClose()
    },
    onError: (e) => {
      if (!live.rejected(e)) toastError(e, t('common.notSaved'))
    },
  })
  return (
    <Dialog
      open
      modal
      onClose={onClose}
      title={memory.id ? t('settings.assistant.memoryBits.editMemory') : t('common.addMemory')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!text.trim() || save.isPending} onClick={() => save.mutate()}>
            {t('common.save')}
          </button>
        </>
      }
    >
      {adding && <EntityDialog id={null} initialKind="person" onClose={() => setAdding(false)} onCreated={(e) => setEntityId(e.id)} />}
      <div className="stack">
        <EditingBanner kind="memory" editing={live.editing} />
        {live.conflict && <ConflictBar kind="memory" conflict={live.conflict} onMine={live.onMine} onTheirs={live.onTheirs} />}
        <div className="field">
          <label htmlFor="mem-text">{t('settings.assistant.memoryBits.whatShouldRemember', { name: assistantNameNow() })}</label>
          <textarea id="mem-text" className="textarea" rows={3} maxLength={1000} value={text} placeholder={t('settings.assistant.memoryBits.eGSamPrefersThai')} onChange={(e) => setText(e.target.value)} />
        </div>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="mem-kind">{t('common.kind')}</label>
            <select id="mem-kind" className="select" value={kind} aria-describedby="mem-kind-example" onChange={(e) => setKind(e.target.value as MemoryKind)}>
              {MEMORY_KINDS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
            <span className="hint" id="mem-kind-example" data-testid="kind-example">{t('memory.dialog.kindExample', { example: kindExample(kind) })}</span>
          </div>
          <div className="field">
            <label htmlFor="mem-ent">{t('settings.assistant.memoryBits.about')}</label>
            <select id="mem-ent" className="select" value={entityId ?? ''} disabled={!!fixedEntity || !!aboutPerson} onChange={(e) => (e.target.value === 'new' ? setAdding(true) : setEntityId(e.target.value ? Number(e.target.value) : null))}>
              <option value="">{aboutPerson && !fixedEntity ? aboutPerson.name || aboutPerson.email : t('memory.dialog.you')}</option>
              {(fixedEntity && !ents.some((e) => e.id === fixedEntity.id) ? [fixedEntity, ...ents] : ents).filter((e) => !e.is_self).map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} ({entityKindLabel(e.kind)})
                </option>
              ))}
              {!fixedEntity && !aboutPerson && <option value="new">{t('memory.dialog.newPersonOrTopic')}</option>}
            </select>
          </div>
        </div>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="mem-exp">{t('settings.assistant.memoryBits.forgetAfterOptional')}</label>
            <input id="mem-exp" type="date" className="input" value={expires} onChange={(e) => setExpires(e.target.value)} />
            <span className="hint">{t('settings.assistant.memoryBits.forThingsLikeAwayUntil')}</span>
          </div>
          <div className="field" style={{ justifyContent: 'center' }}>
            <Check checked={pinned} onChange={setPinned} label={t('common.pin')} desc={t('settings.assistant.memoryBits.alwaysGiveThisTo', { name: assistantNameNow() })} />
          </div>
        </div>
      </div>
    </Dialog>
  )
}

// "Used memories · 3" under an answer or a draft: the notes that went
// into the prompt. It opens to the notes, each one opens to edit, and each can be deleted.
// ACT memory.used @web.panel

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Brain, ChevronDown, ChevronUp, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../../api/client'
import { MemoryDialog, useMemoryMutations } from '../../settings/assistant/memoryBits'
import { qkA, type Memory } from '../../settings/assistant/types'
import { t } from '../../../i18n'

export function UsedMemories({ ids }: { ids: number[] }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Memory | null>(null)
  const qc = useQueryClient()
  const { remove } = useMemoryMutations()
  const q = useQuery<{ items: Memory[]; missing: number }>({
    queryKey: qkA.memoriesById(ids),
    queryFn: () => api.get('/assistant/memories', { ids: ids.join(',') }),
    enabled: open && ids.length > 0,
  })
  if (!ids.length) return null
  const items = q.data?.items ?? []
  const missing = q.data?.missing ?? 0
  return (
    <div className="used-memories" data-testid="used-memories">
      <button type="button" className="used-memories-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Brain size={14} aria-hidden />
        <span>{t('memory.used.label', { count: ids.length })}</span>
        {open ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
      </button>
      {open && (
        <ul className="used-memories-list" style={{ minHeight: q.isLoading ? ids.length * 30 : undefined }}>
          {items.map((m) => (
            <li key={m.id}>
              <button type="button" className="used-memory" onClick={() => setEditing(m)} data-tip={t('settings.assistant.memoryBits.editMemory')}>
                <span className="used-memory-text" dir="auto">{m.text}</span>
                {m.entity && <span className="muted used-memory-about">{m.entity.is_self ? t('memory.dialog.you') : m.entity.name}</span>}
              </button>
              <button type="button" className="icon-btn xs" aria-label={t('settings.assistant.memoryBits.deleteMemory')} data-tip={t('common.delete2')}
                onClick={() => remove.mutate(m, { onSuccess: () => void qc.invalidateQueries({ queryKey: qkA.memoriesById(ids) }) })}>
                <Trash2 size={14} />
              </button>
            </li>
          ))}
          {missing > 0 && <li className="muted">{t('memory.used.missing', { count: missing })}</li>}
        </ul>
      )}
      {editing && <MemoryDialog memory={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

// Files and images attached to a message to the assistant (backend agent/chat_files.py).
// ACT agent.chat_attach @web.panel: the paperclip, a paste or a drop uploads each file at once
// (POST /agent/attachments); the chips above the message box show them until Send, which sends
// their ids with the message. Sent ones show in the chat history: a thumbnail for an image, a chip
// for a file, each a download of what was attached.

import { FileText, Paperclip, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { API, api, ApiError } from '../../../api/client'
import { toast } from '../../../stores/ui'
import { t as tr } from '../../../i18n'

/** An attached file as the server describes it (agent/chat_files.out). */
export interface ChatFile {
  id: string
  name: string
  type: string
  size: number
  kind: 'image' | 'file'
  thumb: boolean
  width?: number
  height?: number
  pages?: number
  format?: string
  truncated?: boolean
  note?: string
}

export const MAX_FILES = 5
export const MAX_BYTES = 15 * 1024 * 1024
/** What the server reads (core/attachfetch.READABLE). The picker offers these; anything else is refused. */
export const ACCEPT = [
  'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf', 'text/*', '.md', '.csv', '.tsv', '.json', '.ics', '.vcf',
  '.eml', '.html', '.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.rtf',
].join(',')
const EXT = /\.(png|jpe?g|gif|webp|pdf|txt|md|markdown|csv|tsv|json|ics|vcf|log|eml|html?|docx|xlsx|pptx|odt|ods|odp|rtf)$/i

/** Whether the server can read this file, by its type or name (the server checks again). */
export function readable(f: { name: string; type: string }): boolean {
  const t = f.type.toLowerCase()
  if (/^image\/(png|jpe?g|gif|webp)$/.test(t) || t === 'application/pdf' || (t.startsWith('text/') && t !== 'text/rtf')) return true
  return EXT.test(f.name)
}

export function sizeText(n: number): string {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
}

export const thumbUrl = (id: string) => `${API}/agent/attachments/${encodeURIComponent(id)}/thumb`
export const fileUrl = (id: string) => `${API}/agent/attachments/${encodeURIComponent(id)}`

interface Pending {
  key: string
  name: string
  size: number
  preview: string | null
  file?: ChatFile
  uploading: boolean
}

let seq = 0

/** The uploads waiting for Send. */
export function usePendingFiles() {
  const [items, setItems] = useState<Pending[]>([])
  const itemsRef = useRef(items)
  itemsRef.current = items

  useEffect(() => () => itemsRef.current.forEach((p) => p.preview && URL.revokeObjectURL(p.preview)), [])

  const add = useCallback((files: File[]) => {
    const room = MAX_FILES - itemsRef.current.length
    if (files.length > room) toast({ message: tr('rail.assistant.attachments.tooMany', { max: MAX_FILES }), error: true })
    for (const f of files.slice(0, Math.max(0, room))) {
      if (!readable(f)) {
        toast({ message: tr('rail.assistant.attachments.notReadable', { name: f.name }), error: true })
        continue
      }
      if (f.size > MAX_BYTES) {
        toast({ message: tr('rail.assistant.attachments.tooLarge', { name: f.name, max: MAX_BYTES / 1048576 }), error: true })
        continue
      }
      const key = `p${++seq}`
      const preview = f.type.startsWith('image/') ? URL.createObjectURL(f) : null
      setItems((list) => [...list, { key, name: f.name || tr('rail.assistant.attachments.pastedImage'), size: f.size, preview, uploading: true }])
      const form = new FormData()
      form.append('file', f, f.name || 'pasted.png')
      api
        .post<ChatFile>('/agent/attachments', form)
        .then((file) => setItems((list) => list.map((p) => (p.key === key ? { ...p, file, uploading: false } : p))))
        .catch((e) => {
          setItems((list) => list.filter((p) => p.key !== key))
          if (preview) URL.revokeObjectURL(preview)
          const why = e instanceof ApiError ? e.message : String(e)
          toast({ message: tr('rail.assistant.attachments.uploadFailed', { name: f.name, error: why }), error: true })
        })
    }
  }, [])

  const remove = useCallback((key: string) => {
    const p = itemsRef.current.find((x) => x.key === key)
    setItems((list) => list.filter((x) => x.key !== key))
    if (p?.preview) URL.revokeObjectURL(p.preview)
    if (p?.file) void api.del(`/agent/attachments/${encodeURIComponent(p.file.id)}`).catch(() => undefined)
  }, [])

  /** The uploaded files, handed to Send; the chips go (their previews stay with the sent turn). */
  const take = useCallback((): ChatFile[] => {
    const files = itemsRef.current.flatMap((p) => (p.file ? [p.file] : []))
    setItems([])
    return files
  }, [])

  const uploading = items.some((p) => p.uploading)
  return { items, add, remove, take, uploading, ready: items.length > 0 && !uploading }
}

/** The paperclip next to the microphone. */
export function AttachButton({ onFiles, disabled }: { onFiles: (files: File[]) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  const label = tr('rail.assistant.attachments.attach')
  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        data-testid="chat-attach-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          if (files.length) onFiles(files)
        }}
      />
      <button type="button" className="chat-mic chat-attach" aria-label={label} data-tip={label} data-testid="chat-attach"
        disabled={disabled} onClick={() => input.current?.click()}>
        <Paperclip size={18} />
      </button>
    </>
  )
}

/** The chips of the uploads waiting for Send, above the message box. */
export function PendingFiles({ items, onRemove }: { items: Pending[]; onRemove: (key: string) => void }) {
  if (!items.length) return null
  return (
    <div className="chat-files pending" data-testid="chat-pending-files">
      {items.map((p) => (
        <span key={p.key} className={`chat-file${p.preview ? ' image' : ''}${p.uploading ? ' uploading' : ''}`} data-testid="chat-pending-file">
          {p.preview ? <img src={p.preview} alt="" /> : <FileText size={16} aria-hidden />}
          <span className="chat-file-name ellipsis" dir="auto">{p.name}</span>
          <span className="chat-file-size">{p.uploading ? <span className="spinner sm" role="status" aria-label={tr('rail.assistant.attachments.uploading')} /> : sizeText(p.size)}</span>
          <button type="button" className="chat-ctx-x" aria-label={tr('rail.assistant.attachments.remove', { name: p.name })} onClick={() => onRemove(p.key)}>
            <X size={12} />
          </button>
        </span>
      ))}
    </div>
  )
}

/** What a sent message carried, in the chat history. */
export function SentFiles({ files }: { files: ChatFile[] }) {
  if (!files.length) return null
  return (
    <div className="chat-files sent" data-testid="chat-sent-files">
      {files.map((f) =>
        f.kind === 'image' && f.thumb ? (
          <a key={f.id} className="chat-file-thumb" href={fileUrl(f.id)} download={f.name} data-tip={f.name}
            aria-label={tr('rail.assistant.attachments.download', { name: f.name })}>
            <img src={thumbUrl(f.id)} alt={tr('rail.assistant.attachments.imageAlt', { name: f.name })} loading="lazy" />
          </a>
        ) : (
          <a key={f.id} className="chat-file" href={fileUrl(f.id)} download={f.name} data-tip={f.note ?? f.name}
            aria-label={tr('rail.assistant.attachments.download', { name: f.name })}>
            <FileText size={16} aria-hidden />
            <span className="chat-file-name ellipsis" dir="auto">{f.name}</span>
            <span className="chat-file-size">{sizeText(f.size)}</span>
          </a>
        ),
      )}
    </div>
  )
}

/** Files on the clipboard (a pasted screenshot) or in a drop. */
export function filesOf(data: DataTransfer | null): File[] {
  if (!data) return []
  const out = Array.from(data.files ?? [])
  if (out.length) return out
  return Array.from(data.items ?? []).flatMap((i) => (i.kind === 'file' ? [i.getAsFile()].filter((f): f is File => f != null) : []))
}

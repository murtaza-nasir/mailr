// Every file across the mailbox, newest first (GET /attachments).
// Filters: kind, file name, and the search box's query. A card opens the file or its email.

import { useInfiniteQuery } from '@tanstack/react-query'
import { Archive, File, FileImage, FileSpreadsheet, FileText, Mail, Presentation } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { EmptyState, Spinner } from '../../components/bits'
import { formatRelative, formatSize } from '../../lib/format'
import { mailPath } from '../../lib/views'
import { t as tr } from '../../i18n'

interface Item {
  message_id: number
  conversation_id: number
  part: number
  filename: string
  type: string
  size: number
  subject: string
  from_name: string | null
  from_email: string
  date: string | null
}

const KINDS: { id: string; label: string; icon: ReactNode }[] = [
  { id: '', label: tr('common.all'), icon: <File size={16} /> },
  { id: 'image', label: tr('mail.attachmentsView.images'), icon: <FileImage size={16} /> },
  { id: 'pdf', label: 'PDFs', icon: <FileText size={16} /> },
  { id: 'document', label: tr('mail.attachmentsView.documents'), icon: <FileText size={16} /> },
  { id: 'spreadsheet', label: tr('mail.attachmentsView.spreadsheets'), icon: <FileSpreadsheet size={16} /> },
  { id: 'presentation', label: tr('mail.attachmentsView.slides'), icon: <Presentation size={16} /> },
  { id: 'archive', label: tr('mail.attachmentsView.archives'), icon: <Archive size={16} /> },
]

function iconFor(it: Item): ReactNode {
  const t = it.type.toLowerCase()
  const n = it.filename.toLowerCase()
  if (t.startsWith('image/')) return <FileImage />
  if (/sheet|excel|csv/.test(t) || /\.(xlsx?|ods|csv)$/.test(n)) return <FileSpreadsheet />
  if (/presentation|powerpoint/.test(t) || /\.(pptx?|odp)$/.test(n)) return <Presentation />
  if (/zip|rar|7z|tar|gzip/.test(t) || /\.(zip|rar|7z|gz|tgz)$/.test(n)) return <Archive />
  if (t === 'application/pdf' || /(word|text|rtf)/.test(t)) return <FileText />
  return <File />
}

const url = (it: Item) => `/api/v1/messages/${it.message_id}/attachments/${it.part}`

export function AttachmentsView({ q }: { q: string }) {
  const nav = useNavigate()
  const [kind, setKind] = useState('')
  const [name, setName] = useState('')
  // The file-name filter asks the server once typing pauses, not on every key.
  const [nameQ, setNameQ] = useState('')
  useEffect(() => {
    const id = window.setTimeout(() => setNameQ(name.trim()), 250)
    return () => window.clearTimeout(id)
  }, [name])
  const list = useInfiniteQuery({
    queryKey: ['attachments', q, kind, nameQ],
    // Keyset pages: the server's next_cursor, not an offset over every earlier file.
    queryFn: ({ pageParam, signal }) =>
      api.get<{ items: Item[]; next_cursor: string | null }>('/attachments', { q, kind, name: nameQ, cursor: pageParam, limit: 60 }, signal),
    initialPageParam: '',
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // Coming back to the tab refetched every page loaded; new files show on the next visit.
    refetchOnWindowFocus: false,
  })
  const items = list.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div className="files-view">
      <div className="files-head">
        <h2>{tr('common.attachments')}{q ? <span className="muted"> · {q}</span> : null}</h2>
        <div className="files-filters" role="radiogroup" aria-label={tr('common.kind')}>
          {KINDS.map((k) => (
            <button key={k.id} type="button" role="radio" aria-checked={kind === k.id} className="chip" aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
              {k.icon}{k.label}
            </button>
          ))}
          <input className="input files-name" type="search" placeholder={tr('mail.attachmentsView.fileName')} aria-label={tr('mail.attachmentsView.fileName')} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
      </div>
      {list.isLoading ? (
        <div className="files-empty"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState icon={<File />} title={tr('mail.attachmentsView.noAttachments')}>
          <p>{kind || nameQ || q ? tr('mail.attachmentsView.nothingMatchesTheseFilters') : tr('mail.attachmentsView.filesPeopleSendYouWill')}</p>
        </EmptyState>
      ) : (
        <div className="files-grid">
          {items.map((it) => (
            <div key={`${it.message_id}-${it.part}`} className="files-card">
              <a className="files-preview" href={url(it)} target="_blank" rel="noopener noreferrer" title={tr('mail.attachmentsView.open', { filename: it.filename })} tabIndex={-1} aria-hidden="true">
                {it.type.startsWith('image/') ? <img src={`${url(it)}?thumb=1`} alt="" loading="lazy" /> : <span className="files-icon">{iconFor(it)}</span>}
              </a>
              <div className="files-meta">
                <a className="files-name-link" href={url(it)} target="_blank" rel="noopener noreferrer" title={it.filename}>{it.filename}</a>
                <div className="muted files-sub" title={it.from_email}>
                  {it.from_name || it.from_email} · {formatRelative(it.date)} · {formatSize(it.size)}
                </div>
                <button type="button" className="btn text sm files-open" title={it.subject} onClick={() => nav(mailPath('all', it.conversation_id))}>
                  <Mail size={14} /> <bdi>{it.subject || tr('common.noSubject')}</bdi>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {list.hasNextPage && (
        <div className="files-more">
          <button type="button" className="btn tonal" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            {list.isFetchingNextPage ? tr('common.loading2') : tr('common.showMore')}
          </button>
        </div>
      )}
    </div>
  )
}

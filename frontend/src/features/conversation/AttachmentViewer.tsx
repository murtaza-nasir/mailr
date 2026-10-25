import { ChevronLeft, ChevronRight, Download, ExternalLink, FileIcon, X } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { Attachment } from '../../api/types'
import { formatSize } from '../../lib/format'
import { t as tr } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

/** How the viewer shows a file. The server serves these inline (it sniffs the real type from
 *  the file name when the sender labelled a PDF "application/octet-stream"). */
export function previewKind(a: Attachment): 'image' | 'pdf' | 'media' | 'text' | null {
  const t = a.type.toLowerCase()
  const ext = a.filename.toLowerCase().split('.').pop() ?? ''
  if (t.startsWith('image/') && t !== 'image/svg+xml') return 'image'
  if (t === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (t.startsWith('video/') || t.startsWith('audio/')) return 'media'
  if (t === 'text/plain' || ['txt', 'log', 'csv', 'md'].includes(ext)) return 'text'
  return null
}

interface Props {
  items: Attachment[]
  index: number
  urlFor: (a: Attachment) => string
  onIndex: (i: number) => void
  onClose: () => void
}

/** Full-window preview (Gmail-style): file name, open-in-tab and download in a top bar,
 *  arrows / ←→ keys to move between the message's attachments, Esc to close. */
export function AttachmentViewer({ items, index, urlFor, onIndex, onClose }: Props) {
  const a = items[index]
  const url = urlFor(a)
  const kind = previewKind(a)
  const closeRef = useRef<HTMLButtonElement>(null)
  const has = items.length > 1

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      } else if (arrowStep(e.key) === -1 && has) {
        e.stopPropagation()
        onIndex((index - 1 + items.length) % items.length)
      } else if (arrowStep(e.key) === 1 && has) {
        e.stopPropagation()
        onIndex((index + 1) % items.length)
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      prev?.focus?.()
    }
  }, [index, items.length, has, onClose, onIndex])

  return createPortal(
    <div className="att-viewer" role="dialog" aria-modal="true" aria-label={tr('conversation.attachmentViewer.previewOf', { filename: a.filename })}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="att-viewer-bar">
        <button ref={closeRef} className="icon-btn" onClick={onClose} aria-label={tr('conversation.attachmentViewer.closePreview')} data-tip={tr('common.closeEsc')}>
          <X />
        </button>
        <div className="att-viewer-title">
          <span className="name">{a.filename}</span>
          <span className="meta">{formatSize(a.size)}{has ? tr('conversation.attachmentViewer.of', { value: index + 1, itemsLength: items.length }) : ''}</span>
        </div>
        <a className="icon-btn" href={url} target="_blank" rel="noopener noreferrer" aria-label={tr('conversation.attachmentViewer.openInNewTab')} data-tip={tr('conversation.attachmentViewer.openInNewTab')}>
          <ExternalLink />
        </a>
        <a className="icon-btn" href={`${url}?download=1`} download={a.filename} aria-label={tr('conversation.attachmentViewer.download', { filename: a.filename })} data-tip={tr('conversation.attachmentViewer.download2')}>
          <Download />
        </a>
      </div>
      <div className="att-viewer-stage" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
        {kind === 'image' && <img src={url} alt={a.filename} />}
        {kind === 'pdf' && <iframe key={url} src={url} title={a.filename} />}
        {kind === 'text' && <iframe key={url} src={url} title={a.filename} className="text" />}
        {kind === 'media' && (a.type.startsWith('audio/')
          ? <audio src={url} controls autoPlay />
          : <video src={url} controls autoPlay />)}
        {kind === null && (
          <div className="att-viewer-none">
            <FileIcon />
            <p>{tr('conversation.attachmentViewer.noPreviewForThisKind')}</p>
            <a className="btn filled" href={`${url}?download=1`} download={a.filename}><Download />{' '}{tr('conversation.attachmentViewer.download2')}</a>
          </div>
        )}
      </div>
      {has && (
        <>
          <button className="att-viewer-nav prev" onClick={() => onIndex((index - 1 + items.length) % items.length)} aria-label={tr('conversation.attachmentViewer.previousAttachment')}><ChevronLeft /></button>
          <button className="att-viewer-nav next" onClick={() => onIndex((index + 1) % items.length)} aria-label={tr('conversation.attachmentViewer.nextAttachment')}><ChevronRight /></button>
        </>
      )}
    </div>,
    document.body,
  )
}

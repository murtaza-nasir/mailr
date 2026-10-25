import { X } from 'lucide-react'
import { formatSize } from '../../lib/format'
import type { PendingAttachment } from './composeModel'
import { t } from '../../i18n'

/** The compose form's attachments: name, size or upload progress, and Remove. */
export function AttachmentList({ atts, onRemove }: { atts: PendingAttachment[]; onRemove: (a: PendingAttachment) => void }) {
  return (
    <div className="compose-atts">
      {atts.map((a) => (
        <div key={a.key} className="compose-att">
          <span className="nm" title={a.filename}>
            {a.filename}
          </span>
          {a.error ? (
            <span className="muted" style={{ color: 'var(--danger)' }}>{a.error}</span>
          ) : a.blob ? (
            <span className="muted">({formatSize(a.size)})</span>
          ) : (
            <span className="progress" aria-label={t('compose.attachmentList.uploading')}>
              <span style={{ width: `${Math.round(a.progress * 100)}%` }} />
            </span>
          )}
          <button type="button" className="icon-btn xs" aria-label={t('compose.attachmentList.remove', { filename: a.filename })} onClick={() => onRemove(a)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  )
}

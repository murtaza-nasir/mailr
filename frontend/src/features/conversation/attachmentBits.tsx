// The kind icon and the always-visible extension of a mail attachment, shared by the reader's file
// list (MessageCard) and the files inside an attached email (AttachedEmails): a program or script
// shows a warning icon there too ("invoice.pdf.exe").

import { File as FileIcon, FileText, Image as ImageIcon, TriangleAlert } from 'lucide-react'
import { attachmentKind } from '../../lib/safeText'
import { t } from '../../i18n'

export function splitExt(name: string): [string, string] {
  const i = name.lastIndexOf('.')
  return i > 0 && name.length - i <= 10 ? [name.slice(0, i), name.slice(i)] : [name, '']
}

export function attIcon(a: { filename: string; type: string }, size?: number) {
  const kind = attachmentKind(a.filename, a.type)
  if (kind === 'risky') return <TriangleAlert size={size} aria-label={t('conversation.messageCard.programOrScript')} className="att-risky" />
  if (kind === 'image') return <ImageIcon size={size} aria-hidden="true" />
  if (kind === 'pdf' || kind === 'text') return <FileText size={size} aria-hidden="true" />
  return <FileIcon size={size} aria-hidden="true" />
}

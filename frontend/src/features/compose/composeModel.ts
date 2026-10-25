/** Types the compose form and its parts share. */
import type { Address, ComposeAttachment } from '../../api/types'

export interface PendingAttachment extends Partial<ComposeAttachment> {
  key: string
  filename: string
  type: string
  size: number
  progress: number
  error?: string
  abort?: () => void
}

let attSeq = 1
/** A key for a new row of the attachment list. */
export function attKey(): string {
  return `a${attSeq++}`
}

/** An attachment the draft already holds, as a row of the list. */
export function heldAttachment(a: ComposeAttachment): PendingAttachment {
  return { ...a, key: attKey(), size: a.size ?? 0, progress: 1 }
}

/** What Undo puts back after the assistant or Help me write changed the draft. */
export interface Snapshot {
  html: string
  subject: string
  to: Address[]
  cc: Address[]
  bcc: Address[]
}

export interface AssistNote {
  title: string
  changes: { old: string; new: string }[]
  undo: Snapshot
  /** The notes Help me write used (the "Used memories" line). */
  memoryIds?: number[]
}

export type RecipientKind = 'to' | 'cc' | 'bcc'
/** Recipients the assistant proposes for an open window; nothing changes until Accept. */
export type RecipientProposal = Partial<Record<RecipientKind, Address[]>>

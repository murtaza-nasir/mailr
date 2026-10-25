// The display model the server sends for what the assistant shows (schema v1,
// spec/fixtures/agent-blocks.schema.json, built by backend/mailr/agent/present.py). Every value
// carries `text`, the server's wording; a value type this client does not know shows that text.

import { t } from '../../../../i18n'
import type { Msg } from './serverText'

export interface LabelRef {
  name: string
  color: string | null
}

export interface Person {
  name: string | null
  email: string
}

export interface Condition {
  field: string
  neg: boolean
  values: string[]
  labels?: LabelRef[]
  text: string
}

export interface ChoiceItem {
  name: string
  description: string
  then: DisplayField[]
  text: string
}

export type DisplayValue =
  | { t: 'text'; text: string; msg?: Msg; msgs?: Msg[] }
  | { t: 'labels'; text: string; items: LabelRef[] }
  | { t: 'people'; text: string; items: Person[]; more: number }
  | { t: 'query'; text: string; raw: string; conditions: Condition[] }
  | { t: 'when'; text: string; start: string | null; end: string | null; all_day: boolean }
  | { t: 'count'; text: string; n: number; noun: string }
  | { t: 'diff'; text: string; before: string; after: string; after_msg?: Msg }
  | { t: 'list'; text: string; items: string[]; more: number; msgs?: (Msg | null)[] }
  | { t: 'quote'; text: string }
  | { t: 'choices'; text: string; items: ChoiceItem[] }
  | { t: string; text: string }

export interface DisplayField {
  label: string
  label_msg?: Msg
  value: DisplayValue
}

export type DisplayIcon =
  | 'filter' | 'order' | 'trash' | 'spam' | 'delete' | 'archive' | 'label' | 'snooze' | 'mail' | 'send' | 'event'
  | 'task' | 'memory' | 'skill' | 'settings' | 'people' | 'recording' | 'unsubscribe' | 'draft' | 'note' | 'action'

export interface Display {
  v: number
  icon: DisplayIcon | string
  title: string
  summary: string | null
  danger: boolean
  reason: string | null
  fields: DisplayField[]
  text: string
  title_msg?: Msg
  summary_msg?: Msg
  reason_msg?: Msg
}

export type OutcomeState = 'pending' | 'approved' | 'executing' | 'executed' | 'denied' | 'expired' | 'failed'

export interface Outcome {
  state: OutcomeState | string
  text: string
  msg?: Msg
}

/** Wording for a status the client learned before the server's outcome arrived. */
export function localOutcome(status: string): Outcome {
  const text: Record<string, string> = {
    pending: t('rail.assistant.present.types.waitingForYourApproval'),
    approved: t('rail.assistant.present.types.approvedWorkingOnIt'),
    executing: t('rail.assistant.present.types.approvedWorkingOnIt'),
    executed: t('rail.assistant.present.types.approvedDone'),
    denied: t('rail.assistant.present.types.denied'),
    expired: t('rail.assistant.present.types.expiredWithoutAnAnswer'),
    failed: t('rail.assistant.present.types.approvedButItFailed'),
  }
  return { state: status, text: text[status] ?? status }
}

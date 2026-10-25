// Server texts kept as a key: an approval card's title,
// summary, reason, field labels and fixed values, its outcome, and the assistant's tool steps come
// with a msg ({key, params}) beside their English. This app renders the msg with its own t(), from
// the copy of the server's `approvals` section in en.json (`serverText.*`, written by
// tools/i18n_server_texts.py). A key this app does not have, or no msg at all, shows the English.

import { appLocale, t, type Params } from '../../../../i18n'
import { SERVER_KEYS } from './serverKeys'
import { assistantNameNow } from '../assistantName'

/** Server texts that name the assistant ({{name}}). A card stored before the server sent the name
 *  gets the name the app shows. */
const NAMES_ASSISTANT = new Set(['approvals.forward.summary'])

export interface Msg {
  key: string
  params?: Record<string, unknown>
}

export function isMsg(v: unknown): v is Msg {
  return !!v && typeof v === 'object' && typeof (v as Msg).key === 'string'
}

function render(m: Msg): string | null {
  const key = SERVER_KEYS[m.key]
  if (!key) return null
  const params: Params = {}
  for (const [k, v] of Object.entries(m.params ?? {})) {
    if (isMsg(v)) {
      const inner = render(v)
      if (inner === null) return null
      params[k] = inner
    } else if (v === null || ['string', 'number', 'boolean'].includes(typeof v)) {
      params[k] = v as string | number | boolean | null
    }
  }
  if (params.name === undefined && NAMES_ASSISTANT.has(m.key)) params.name = assistantNameNow()
  return t(key, params)
}

/** The msg in this app's language, else `fallback` (the server's English). */
export function msgText(m: unknown, fallback: string): string {
  return isMsg(m) ? (render(m) ?? fallback) : fallback
}

/** Several msgs as one list ("a card on the email, a notification"), else `fallback`. */
export function msgsText(ms: unknown, fallback: string): string {
  if (!Array.isArray(ms) || !ms.length || !ms.every(isMsg)) return fallback
  const parts = ms.map((m) => render(m))
  if (parts.some((p) => p === null)) return fallback
  try {
    return new Intl.ListFormat(appLocale(), { style: 'short', type: 'unit' }).format(parts as string[])
  } catch {
    return parts.join(', ')
  }
}

/** An approval card's title in this app's language. */
export function displayTitle(d: { title: string; title_msg?: unknown }): string {
  return msgText(d.title_msg, d.title)
}

/** A waiting card asks: "Send to Marco Silva?", "Delete “Weekly sync”?". The server's title
 * says what will happen; a decided card keeps it as a statement. */
export function asQuestion(title: string): string {
  const s = title.trim()
  return !s || /[?!.…]$/.test(s) ? s : t('rail.assistant.approvalCard.question', { title: s })
}

// Folders inside each account.
// A folder made in an account is a label whose home is that account. Filing mail of another
// account into it is allowed: it is the same label. The mail stays in the account it arrived
// through, and nothing is copied to the folder's account on its server; the toast says so.

import type { ActionRequest, ConversationSummary, Label, MailAccount } from '../api/types'

/** The account whose folder this filing puts other accounts' mail into, else null: the request
 *  adds (or moves to) a label made in an account, and one of the conversations belongs only to
 *  other accounts. Only with two or more accounts; conversations whose accounts are unknown do not
 *  count. */
export function filedFromOtherAccount(req: Pick<ActionRequest, 'action' | 'label_ids' | 'label_id'>, labels: Label[],
  convs: (Pick<ConversationSummary, 'accounts'> | undefined)[], accounts: MailAccount[]): MailAccount | null {
  if ((req.action !== 'add_labels' && req.action !== 'move') || accounts.length < 2) return null
  const ids = req.action === 'move' ? (req.label_id != null ? [req.label_id] : []) : (req.label_ids ?? [])
  for (const l of labels) {
    if (!ids.includes(l.id) || !l.home_account_id) continue
    const home = l.home_account_id
    if (convs.some((c) => !!c?.accounts?.length && !c.accounts.includes(home)))
      return accounts.find((a) => a.id === home) ?? null
  }
  return null
}

// ---------- pinned folders (8 Oct 2026) ----------
// An account's group shows its system folders, then the folders pinned to that account in the
// user's order, then one row "More folders (n)" that opens to the rest as a tree. The pins live in
// the setting `account_folder_pins` ({"<account id>": [label id, …]}).

export type FolderPins = Record<string, number[]>

/** The pinned label ids of one account, in order. */
export function pinsOf(pins: FolderPins | undefined, accountId: number): number[] {
  const v = pins?.[String(accountId)]
  return Array.isArray(v) ? v : []
}

/** The pins with `labelId` pinned to the account: before `before` (a pinned id), else at the end.
 *  Pinning a pinned folder moves it. */
export function pinFolder(pins: FolderPins | undefined, accountId: number, labelId: number, before: number | null = null): FolderPins {
  const list = pinsOf(pins, accountId).filter((id) => id !== labelId)
  const at = before === null ? -1 : list.indexOf(before)
  if (at < 0) list.push(labelId)
  else list.splice(at, 0, labelId)
  return { ...(pins ?? {}), [String(accountId)]: list }
}

/** The pins without `labelId` in the account (an emptied account goes away). */
export function unpinFolder(pins: FolderPins | undefined, accountId: number, labelId: number): FolderPins {
  const out = { ...(pins ?? {}) }
  const list = pinsOf(pins, accountId).filter((id) => id !== labelId)
  if (list.length) out[String(accountId)] = list
  else delete out[String(accountId)]
  return out
}

/** One step up (-1) or down (1) among the account's pinned folders that are shown (`among`, the
 *  ids of deleted labels left out). Unchanged at either end. */
export function movePin(pins: FolderPins | undefined, accountId: number, labelId: number, delta: -1 | 1, among?: readonly number[]): FolderPins {
  const all = pinsOf(pins, accountId)
  const order = among ? all.filter((id) => among.includes(id)) : all
  const i = order.indexOf(labelId)
  const j = i + delta
  if (i < 0 || j < 0 || j >= order.length) return pins ?? {}
  const next = [...order]
  next.splice(i, 1)
  next.splice(j, 0, labelId)
  // The ids left out of `among` keep their places at the end.
  return { ...(pins ?? {}), [String(accountId)]: [...next, ...all.filter((id) => !order.includes(id))] }
}

export interface FolderSplit {
  /** The pinned folders, in the user's order (labels that no longer exist dropped). */
  pinned: Label[]
  /** Unpinned folders shown above "More folders": those with unread mail while the option is on,
   *  and the open folder while "More folders" is closed. Tree order. */
  surfaced: Label[]
  /** The folders under "More folders". */
  rest: Label[]
  /** The unread mail of the folders under "More folders" (their sum; hidden labels left out). */
  restUnread: number
}

/**
 * Split an account's folders (`folders`, from accountLabels) into the pinned ones, those shown
 * above "More folders", and the rest. A pinned label shows even when it holds none of the
 * account's mail (`labels`, every label, finds it). `unread` gives a folder's unread mail in this
 * account. `current` is the label id of the open view in this account, if any.
 */
export function splitAccountFolders(folders: Label[], labels: Label[], pinIds: readonly number[], unread: (l: Label) => number,
  opts: { showUnread: boolean; moreOpen: boolean; current?: number | null }): FolderSplit {
  const byId = new Map(labels.filter((l) => !l.role).map((l) => [l.id, l]))
  const pinned = pinIds.map((id) => byId.get(id)).filter((l): l is Label => !!l)
  const pinnedIds = new Set(pinned.map((l) => l.id))
  const unpinned = folders.filter((l) => !l.role && !pinnedIds.has(l.id)).sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  const surfaced: Label[] = []
  const rest: Label[] = []
  for (const l of unpinned) {
    const lit = opts.showUnread && l.show_in_list !== 'hide' && unread(l) > 0
    if (lit || (!opts.moreOpen && opts.current === l.id)) surfaced.push(l)
    else rest.push(l)
  }
  const restUnread = rest.reduce((n, l) => n + (l.show_in_list === 'hide' ? 0 : unread(l)), 0)
  return { pinned, surfaced, rest, restUnread }
}

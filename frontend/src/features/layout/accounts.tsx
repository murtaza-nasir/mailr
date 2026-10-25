// Several mail accounts in one mailbox (backend core/mailaccounts.py).
//
// The mailbox is unified: every list holds the mail of every account, so the navigation's heading
// says "All accounts" (one account: its address). With Settings → Mail → Inbox → "Show each
// account separately" on and two or more accounts, every theme adds a group per account with its
// own Inbox, Sent, Drafts, Scheduled, Spam and Trash (views `acct:<id>:<view>`, lib/views.ts).
// Below those come the account's folders: the labels that hold its mail and the labels made in it
// (`home_account_id`), each the label seen through that account (`acct:<id>:label:<label id>`).
// A label is one label for every account: "Receipts" under two accounts is the same label, each
// group showing its own account's mail.
// Each account has a colour: a dot in its group and, in the unified list, on the rows of its mail.

import { ChevronDown, ChevronRight, Clock, EllipsisVertical, File, FolderPlus, Inbox, OctagonAlert, Send, Trash2, X } from 'lucide-react'
import { Fragment, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { useLabelList, useMe, useUpdateSettings } from '../../api/hooks'
import type { ConversationSummary, Label, MailAccount } from '../../api/types'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { accountLabelView, accountView, type AccountViewBase } from '../../lib/views'
import { t } from '../../i18n'
import { useSidebarCounts, type ViewCount } from './sidebarCounts'
import type { NavEntry } from './mailboxes'

/** The palette of account colours (the server's ACCOUNT_HUES); styles/list.css draws each hue in a
 *  light and a dark tone. */
export const ACCOUNT_HUES = ['blue', 'orange', 'green', 'purple', 'pink', 'teal', 'red', 'yellow'] as const

export function hueName(hue: string): string {
  const names: Record<string, string> = {
    blue: t('layout.accounts.blue'), orange: t('layout.accounts.orange'), green: t('layout.accounts.green'),
    purple: t('layout.accounts.purple'), pink: t('layout.accounts.pink'), teal: t('layout.accounts.teal'),
    red: t('layout.accounts.red'), yellow: t('layout.accounts.yellow'),
  }
  return names[hue] ?? hue
}

export interface MailAccounts {
  accounts: MailAccount[]
  byId: Map<number, MailAccount>
  /** Two or more accounts: rows show their account's dot. */
  multi: boolean
  /** The accounts' own folders are shown (the setting is on and there are two or more). */
  separate: boolean
}

export function useMailAccounts(): MailAccounts {
  const accounts = useSidebarCounts().data?.accounts ?? EMPTY
  const on = !!useMe().data?.settings?.account_folders
  const multi = accounts.length > 1
  return { accounts, byId: new Map(accounts.map((a) => [a.id, a])), multi, separate: multi && on }
}

const EMPTY: MailAccount[] = []

/** The title over the unified mailbox: "All accounts" with two or more accounts, the address with
 *  one, `fallback` (Mailbox) before any account is connected. */
export function mailboxHeading(accounts: MailAccount[], fallback: string): string {
  if (accounts.length > 1) return t('layout.accounts.allAccounts')
  if (accounts.length === 1) return accounts[0].address
  return fallback
}

export function useMailboxHeading(fallback: string): string {
  return mailboxHeading(useMailAccounts().accounts, fallback)
}

/** An account's colour as a dot. `label` names the account for screen readers and the tooltip. */
export function AccountDot({ account, label = true, className = '' }: { account: Pick<MailAccount, 'color' | 'address'>; label?: boolean; className?: string }) {
  const name = t('layout.accounts.accountName', { address: account.address })
  return (
    <span
      className={`acct-dot${className ? ` ${className}` : ''}`}
      data-hue={account.color}
      {...(label ? { role: 'img', 'aria-label': name, 'data-tip': name } : { 'aria-hidden': true })}
    />
  )
}

/** The dots of the accounts a conversation belongs to, on a row of the unified list (only with two
 *  or more accounts; the server leaves `accounts` out otherwise). */
export function RowAccounts({ c }: { c: Pick<ConversationSummary, 'accounts'> }) {
  const { byId, multi } = useMailAccounts()
  // Inside an account's own folder every row is that account's: no dots there.
  const inAccount = useLocation().pathname.startsWith('/mail/acct')
  if (!multi || inAccount || !c.accounts?.length) return null
  const shown = c.accounts.map((id) => byId.get(id)).filter((a): a is MailAccount => !!a).slice(0, 3)
  if (!shown.length) return null
  return (
    <span className="row-accounts" data-testid="row-accounts">
      {shown.map((a) => <AccountDot key={a.id} account={a} />)}
    </span>
  )
}

const FOLDER_ICONS: Record<AccountViewBase, ReactNode> = {
  inbox: <Inbox />, sent: <Send />, drafts: <File />, scheduled: <Clock />, spam: <OctagonAlert />, trash: <Trash2 />,
}

const FOLDER_TITLES = (): Record<AccountViewBase, string> => ({
  inbox: t('common.inbox'), sent: t('common.sent'), drafts: t('common.drafts'), scheduled: t('common.scheduled'),
  spam: t('common.spam'), trash: t('common.trash'),
})

export interface AccountGroup {
  account: MailAccount
  entries: NavEntry[]
  /** Unread mail in the account's Inbox, for a collapsed group's heading. */
  unread: number
  /** The account's folders below its system folders (accountLabels). */
  labels: Label[]
  /** The server's counts by view, for the folders' numbers (`acct:<id>:label:<label id>`). */
  views: Record<string, ViewCount>
}

/** The labels an account's group lists: those that hold the account's mail (the server counts them,
 *  GET /counts `acct:<id>:label:<label id>`), those made in it (`home_account_id`), and the labels
 *  above either, so the tree keeps its shape. */
export function accountLabels(labels: Label[], accountId: number, views: Record<string, ViewCount>): Label[] {
  const own = labels.filter((l) => !l.role)
  const byName = new Map(own.map((l) => [l.name, l]))
  const keep = new Set<number>()
  for (const l of own) {
    if (l.home_account_id !== accountId && !(views[accountLabelView(accountId, l.id)]?.total ?? 0)) continue
    keep.add(l.id)
    const parts = l.name.split('/')
    for (let i = 1; i < parts.length; i++) {
      const up = byName.get(parts.slice(0, i).join('/'))
      if (up) keep.add(up.id)
    }
  }
  return own.filter((l) => keep.has(l.id))
}

/** The navigation groups of the accounts' own folders, with the server's counts (GET /counts views
 *  `acct:<id>:<view>`); empty unless the folders are shown. */
// ACT mail.account_view @web.nav (each theme draws these groups; Focus through the palette)
export function useAccountGroups(): AccountGroup[] {
  const { accounts, separate } = useMailAccounts()
  const views = useSidebarCounts().data?.views ?? NO_VIEWS
  const allLabels = useLabelList()
  const folders = useMemo(() => new Map(accounts.map((a) => [a.id, accountLabels(allLabels, a.id, views)])), [accounts, allLabels, views])
  if (!separate) return []
  const titles = FOLDER_TITLES()
  return accounts.map((account) => {
    const n = (v: AccountViewBase) => views[accountView(account.id, v)]
    const inbox = n('inbox')
    const drafts = n('drafts')
    const spam = n('spam')
    const sched = n('scheduled')
    const entries: NavEntry[] = [
      { view: accountView(account.id, 'inbox'), title: titles.inbox, icon: FOLDER_ICONS.inbox, count: inbox?.unread || undefined,
        bold: (inbox?.unread ?? 0) > 0, unread: inbox?.unread, total: inbox?.total },
      { view: accountView(account.id, 'sent'), title: titles.sent, icon: FOLDER_ICONS.sent },
      { view: accountView(account.id, 'drafts'), title: titles.drafts, icon: FOLDER_ICONS.drafts, count: drafts?.total || undefined,
        bold: (drafts?.total ?? 0) > 0, total: drafts?.total },
      { view: accountView(account.id, 'scheduled'), title: titles.scheduled, icon: FOLDER_ICONS.scheduled, count: sched?.total || undefined, total: sched?.total },
      { view: accountView(account.id, 'spam'), title: titles.spam, icon: FOLDER_ICONS.spam, count: spam?.unread || undefined,
        bold: (spam?.unread ?? 0) > 0, unread: spam?.unread, total: spam?.total },
      { view: accountView(account.id, 'trash'), title: titles.trash, icon: FOLDER_ICONS.trash },
    ]
    return { account, entries, unread: inbox?.unread ?? 0, labels: folders.get(account.id) ?? [], views }
  })
}

const NO_VIEWS: Record<string, ViewCount> = {}

/** Where the hint's link goes: the setting's row in Settings → Mail → Inbox. */
export const ACCOUNT_FOLDERS_SETTING = '/settings/mail/inbox#account-folders'

/**
 * A one-line hint in the navigation, where the account groups would be: with two or more mail
 * accounts, "Show each account separately" is offered until the user dismisses it or changes the
 * setting (settings.account_folders_hint_dismissed; Settings → Mail → Inbox sets it too). It is no
 * navigation entry: a link to the setting and a ✕. Not in Focus (it has no navigation column).
 */
export function AccountFoldersHint({ className = '', onNavigate }: { className?: string; onNavigate?: () => void }) {
  const { multi } = useMailAccounts()
  const settings = useMe().data?.settings
  const upd = useUpdateSettings()
  if (!multi || !settings || settings.account_folders || settings.account_folders_hint_dismissed) return null
  const dismiss = () => upd.mutate({ account_folders_hint_dismissed: true })
  return (
    <div className={`acct-hint${className ? ` ${className}` : ''}`} data-testid="acct-folders-hint">
      <Link to={ACCOUNT_FOLDERS_SETTING} className="acct-hint-link" data-tip={t('layout.accounts.hintTip')} onClick={onNavigate}>
        {t('settings.inboxTab.showEachAccountSeparately')}
      </Link>
      <button type="button" className="icon-btn xs acct-hint-close" aria-label={t('layout.accounts.dismissHint')} data-tip={t('layout.accounts.dismissHint')} onClick={dismiss}>
        <X size={14} />
      </button>
    </div>
  )
}

/** The ⋮ beside an account's heading: New folder (a label made in that account). */
export function AccountGroupMenu({ account, onNewFolder }: { account: MailAccount; onNewFolder: (a: MailAccount) => void }) {
  const menu = useAnchor()
  return (
    <>
      <button type="button" className="icon-btn xs more-btn acct-more" aria-label={t('layout.accounts.optionsFor', { address: account.address })}
        data-tip={t('layout.accounts.optionsFor', { address: account.address })} data-testid={`acct-menu-${account.id}`}
        onClick={(e) => menu.toggle(e.currentTarget)}>
        <EllipsisVertical size={16} />
      </button>
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="right-start" minWidth={200}>
        {/* ACT mail.account_folder_create @web.nav */}
        <MenuItem icon={<FolderPlus />} onSelect={() => { menu.close(); onNewFolder(account) }}>
          {t('layout.accounts.newFolder')}
        </MenuItem>
      </Popover>
    </>
  )
}

/** The heading button of an account's group: caret, colour dot, address, and the Inbox's unread
 *  count while it is collapsed. Shared by the sidebar-style navs. */
export function AccountGroupToggle({ g, open, onToggle, className = 'nav-item nav-group-toggle', style }: { g: AccountGroup; open: boolean; onToggle: () => void; className?: string; style?: CSSProperties }) {
  return (
    <button type="button" className={`${className} acct-group-toggle`} onClick={onToggle} aria-expanded={open} style={style}
      data-tip={g.account.address} data-testid={`acct-group-${g.account.id}`}>
      <span className="caret">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      {/* The dot sits in the icon's place and the address right after it, at the start (RTL too). */}
      <span className="acct-lead"><AccountDot account={g.account} label={false} /></span>
      <bdi className="name">{g.account.address}</bdi>
      {!open && g.unread > 0 && <span className="count">{g.unread}</span>}
    </button>
  )
}

/** One account's group: its heading (with ⋮ → New folder when `onNewFolder` is given) and, open,
 *  its system folders (`row` draws each the theme's way) and then its labels (`folders`).
 *  Open or closed is remembered per account and theme (`storage`). */
export function AccountGroupNav({ g, storage, row, folders, onNewFolder, toggleClassName, toggleStyle, initial = true }: {
  g: AccountGroup
  storage: string
  row: (e: NavEntry) => ReactNode
  folders?: (g: AccountGroup) => ReactNode
  onNewFolder?: (a: MailAccount) => void
  toggleClassName?: string
  toggleStyle?: CSSProperties
  initial?: boolean
}) {
  const [open, toggle] = useStoredOpen(`${storage}.${g.account.id}`, initial)
  return (
    <div className="acct-group" role="group" aria-label={g.account.address} data-account={g.account.id}>
      <div className="acct-group-head">
        <AccountGroupToggle g={g} open={open} onToggle={toggle} className={toggleClassName} style={toggleStyle} />
        {onNewFolder && <AccountGroupMenu account={g.account} onNewFolder={onNewFolder} />}
      </div>
      {open && g.entries.map((e) => <Fragment key={e.view}>{row(e)}</Fragment>)}
      {open && folders?.(g)}
    </div>
  )
}

function useStoredOpen(key: string, initial: boolean): [boolean, () => void] {
  const [v, setV] = useState(() => {
    try {
      const s = localStorage.getItem(key)
      return s === null ? initial : s === '1'
    } catch {
      return initial
    }
  })
  return [v, () => {
    setV(!v)
    try {
      localStorage.setItem(key, v ? '0' : '1')
    } catch {
      /* private mode */
    }
  }]
}

// ACT mail.archive @web.list @web.reader, ACT mail.delete @web.list @web.reader, ACT mail.spam @web.list @web.reader, ACT mail.not_spam @web.list @web.reader, ACT mail.untrash (Move to folder's Inbox, Archive, Spam and Trash)
// The conversation menus: Label as, Move to folder, Snooze and Remind me if no reply. Each list body is
// shared: the standalone menu (the toolbar's buttons and the b / v / l keys) wraps it in a
// Popover, and the row's and the reader's menus show it in a submenu (components/Popover.tsx
// SubMenu). A body that opens a dialog hands it to its owner (onCustom, onCreate): a submenu's
// content unmounts when the menu closes, the dialog must not.

import { AlarmClock, Archive, BellOff, BellRing, CalendarClock, Inbox, Plus, Settings2, Tag, Trash2, OctagonAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useActions, useLabelMutations, useLabelList } from '../../api/hooks'
import type { ConversationSummary, Label, MailAccount } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { MenuHeader, MenuItem, MenuSep, Popover } from '../../components/Popover'
import { followUpOptions, formatOptionTime, snoozeOptions, toLocalInput } from '../../lib/snooze'
import { useUI } from '../../stores/ui'
import { t } from '../../i18n'
import { viewLabelOf } from '../../lib/views'

interface MenuBase {
  anchor: HTMLElement | DOMRect | null
  open: boolean
  onClose: () => void
  ids: number[]
  /** Summaries of the targeted conversations (for checkbox state). */
  items?: ConversationSummary[]
  onDone?: () => void
}

function useFilteredLabels(filter: string) {
  const labels = useLabelList()
  return useMemo(() => {
    const user = labels.filter((l) => !l.role).sort((a, b) => a.name.localeCompare(b.name))
    const f = filter.trim().toLowerCase()
    return f ? user.filter((l) => l.name.toLowerCase().includes(f)) : user
  }, [labels, filter])
}

// ---------- Label as ----------

interface LabelChoicesProps {
  ids: number[]
  items?: ConversationSummary[]
  /** Close the whole menu. */
  onClose: () => void
  onDone?: () => void
  /** "New label…": the owner opens the dialog (with the filter's text as the name). Ticks not yet
   *  applied still apply when the list closes. */
  onCreate: (name: string) => void
}

/**
 * The tri-state label list: ticking only marks the change, so several can be ticked; the
 * changes apply on Apply, on Enter in the filter, or when the list closes (as Gmail does).
 * ACT mail.label @web.list @web.reader
 */
export function LabelChoices({ ids, items = [], onClose, onDone, onCreate }: LabelChoicesProps) {
  const [filter, setFilter] = useState('')
  const labels = useFilteredLabels(filter)
  const { run } = useActions()
  const initial = useMemo(() => {
    const m = new Map<number, 'all' | 'some'>()
    if (!items.length) return m
    const counts = new Map<number, number>()
    for (const c of items) for (const l of c.label_ids) counts.set(l, (counts.get(l) ?? 0) + 1)
    for (const [l, n] of counts) m.set(l, n === items.length ? 'all' : 'some')
    return m
  }, [items])
  const [changes, setChanges] = useState<Map<number, boolean>>(new Map())

  const state = (l: Label): boolean | 'mixed' => {
    if (changes.has(l.id)) return changes.get(l.id)!
    const s = initial.get(l.id)
    return s === 'all' ? true : s === 'some' ? 'mixed' : false
  }

  const commit = async (ch: Map<number, boolean>) => {
    const add = [...ch].filter(([, v]) => v).map(([k]) => k)
    const remove = [...ch].filter(([, v]) => !v).map(([k]) => k)
    if (add.length) await run({ ids, action: 'add_labels', label_ids: add }, { silent: remove.length > 0 })
    if (remove.length) await run({ ids, action: 'remove_labels', label_ids: remove })
    onDone?.()
  }
  // Pending ticks apply when the list goes away (the menu closed, or the pointer left the submenu).
  const pending = useRef({ changes, commit })
  pending.current = { changes, commit }
  useEffect(() => () => {
    const p = pending.current
    if (p.changes.size) void p.commit(p.changes)
  }, [])
  const applyAndClose = () => {
    const ch = changes
    pending.current = { changes: new Map(), commit }
    setChanges(new Map())
    onClose()
    void commit(ch)
  }

  return (
    <>
      <div className="menu-search">
        <input
          data-autofocus
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t('mail.menus.searchLabels')}
          aria-label={t('mail.menus.searchLabels')}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (changes.size) applyAndClose()
              else if (labels.length === 1) {
                onClose()
                void run({ ids, action: 'add_labels', label_ids: [labels[0].id] }).then(() => onDone?.())
              }
            }
          }}
        />
      </div>
      <div className="menu-scroll">
        {labels.map((l) => (
          <MenuItem key={l.id} checked={state(l)} onSelect={() => setChanges(new Map(changes).set(l.id, state(l) !== true))}>
            {l.name}
          </MenuItem>
        ))}
        {labels.length === 0 && <div className="menu-header">{t('mail.menus.noLabelsMatch')}</div>}
      </div>
      <MenuSep />
      {changes.size > 0 && (
        <MenuItem onSelect={applyAndClose}>
          <b>{t('common.apply')}</b>
        </MenuItem>
      )}
      <MenuItem icon={<Plus />} onSelect={() => onCreate(filter.trim())}>
        {filter.trim() ? t('mail.menus.newLabel', { filter: filter.trim() }) : t('mail.menus.newLabel2')}
      </MenuItem>
      <ManageLabelsItem onClose={() => { pending.current = { changes: new Map(), commit }; onClose() }} />
    </>
  )
}

/** "New label…" from a label list: create it, then put it on the conversations. */
export function NewLabelDialog({ name, ids, onClose, onDone }: { name: string | null; ids: number[]; onClose: () => void; onDone?: () => void }) {
  const { run } = useActions()
  return (
    <CreateLabelDialog
      key={name ?? ''}
      open={name !== null}
      initialName={name ?? ''}
      onClose={onClose}
      onCreated={(l) => {
        onClose()
        void run({ ids, action: 'add_labels', label_ids: [l.id] }).then(() => onDone?.())
      }}
    />
  )
}

export function LabelMenu({ anchor, open, onClose, ids, items = [], onDone }: MenuBase) {
  const [creating, setCreating] = useState<string | null>(null)
  return (
    <>
      <Popover anchor={anchor} open={open} onClose={onClose} label={t('mail.menus.labelAs')} minWidth={260}>
        <MenuHeader>{t('mail.menus.labelAs2')}</MenuHeader>
        <LabelChoices ids={ids} items={items} onClose={onClose} onDone={onDone} onCreate={setCreating} />
      </Popover>
      <NewLabelDialog name={creating} ids={ids} onClose={() => setCreating(null)} onDone={onDone} />
    </>
  )
}

function ManageLabelsItem({ onClose }: { onClose: () => void }) {
  const nav = useNavigate()
  return (
    <MenuItem
      icon={<Settings2 />}
      onSelect={() => {
        onClose()
        nav('/settings/mail/labels')
      }}
    >
      {t('mail.menus.manageLabels')}
    </MenuItem>
  )
}

// ---------- Move to folder ----------

/** What "Move to folder" (v) means, under its name in every menu. */
export const MOVE_TO_FOLDER_HINT = t('mail.menus.removesFromTheInbox')

/**
 * "Move to folder": where a conversation can go from this view: Inbox (or out of Trash / Spam), Archive, Spam and
 * Trash as they apply, then the labels with a filter box. Enter moves to the only match.
 * ACT mail.move @web.list @web.reader
 */
export function MoveChoices({ ids, currentView, onClose, onDone }: { ids: number[]; currentView: string; onClose: () => void; onDone?: () => void }) {
  const [filter, setFilter] = useState('')
  const labels = useFilteredLabels(filter)
  const { run } = useActions()
  const doMove = (fn: () => Promise<unknown>) => {
    onClose()
    void fn().then(() => onDone?.())
  }
  const fromLabel = viewLabelOf(currentView)
  const toLabel = (l: Label) => doMove(() => run({ ids, action: 'move', label_id: l.id, ...(fromLabel ? { from_label_id: fromLabel } : {}) }))
  const archivable = currentView === 'inbox' || fromLabel !== null
  return (
    <>
      <div className="menu-hint">{MOVE_TO_FOLDER_HINT}</div>
      {currentView !== 'inbox' && (
        <MenuItem icon={<Inbox />} onSelect={() => doMove(() => run({ ids, action: currentView === 'trash' ? 'untrash' : currentView === 'spam' ? 'not_spam' : 'inbox' }))}>
          {t('common.inbox')}
        </MenuItem>
      )}
      {archivable && (
        <MenuItem icon={<Archive />} onSelect={() => {
          const from = fromLabel ?? useUI.getState().contextLabelId
          doMove(() => run({ ids, action: 'archive', ...(from ? { from_label_id: from } : {}) }))
        }}>
          {t('common.archive')}
        </MenuItem>
      )}
      {currentView !== 'spam' && (
        <MenuItem icon={<OctagonAlert />} onSelect={() => doMove(() => run({ ids, action: 'spam' }))}>
          {t('common.spam')}
        </MenuItem>
      )}
      {currentView !== 'trash' && (
        <MenuItem icon={<Trash2 />} onSelect={() => doMove(() => run({ ids, action: 'trash' }))}>
          {t('common.trash')}
        </MenuItem>
      )}
      <MenuSep />
      <div className="menu-search">
        <input
          data-autofocus
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t('mail.menus.searchLabels')}
          aria-label={t('mail.menus.searchLabels')}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && labels.length === 1) {
              e.preventDefault()
              toLabel(labels[0])
            }
          }}
        />
      </div>
      <div className="menu-scroll">
        {labels.map((l) => (
          <MenuItem key={l.id} icon={<Tag />} onSelect={() => toLabel(l)}>
            {l.name}
          </MenuItem>
        ))}
        {labels.length === 0 && <div className="menu-header">{t('mail.menus.noLabelsMatch')}</div>}
      </div>
    </>
  )
}

export function MoveMenu({ anchor, open, onClose, ids, onDone, currentView }: MenuBase & { currentView: string }) {
  return (
    <Popover anchor={anchor} open={open} onClose={onClose} label={t('common.moveToFolder')} minWidth={240}>
      <MenuHeader>{t('mail.menus.moveToFolder')}</MenuHeader>
      <MoveChoices ids={ids} currentView={currentView} onClose={onClose} onDone={onDone} />
    </Popover>
  )
}

// ---------- Snooze ----------

/** Snooze these conversations until d (ACT mail.snooze @web.list @web.reader). */
export function useSnooze(ids: number[], onDone?: () => void) {
  const { run } = useActions()
  return (d: Date) => {
    void run({ ids, action: 'snooze', until: d.toISOString() }, { message: t('mail.menus.snoozedUntil', { optionTime: formatOptionTime(d) }) }).then(() => onDone?.())
  }
}

/** The snooze presets with their times, then "Pick date & time…" (onCustom: the owner closes the
 *  menu, or not, and shows its dialog). */
export function SnoozeChoices({ ids, onClose, onDone, onCustom }: { ids: number[]; onClose: () => void; onDone?: () => void; onCustom: () => void }) {
  const snooze = useSnooze(ids, onDone)
  return (
    <>
      {snoozeOptions().map((o) => (
        <MenuItem key={o.id} icon={<AlarmClock />} onSelect={() => { onClose(); snooze(o.at) }} shortcut={formatOptionTime(o.at)}>
          {o.label}
        </MenuItem>
      ))}
      <MenuSep />
      <MenuItem icon={<CalendarClock />} onSelect={onCustom}>
        {t('mail.menus.pickDateTime2')}
      </MenuItem>
    </>
  )
}

export function SnoozeMenu({ anchor, open, onClose, ids, onDone }: MenuBase) {
  const [custom, setCustom] = useState(false)
  const snooze = useSnooze(ids, onDone)
  return (
    <>
      <Popover anchor={anchor} open={open && !custom} onClose={onClose} label={t('mail.menus.snoozeUntil')} minWidth={300}>
        <MenuHeader>{t('mail.menus.snoozeUntil2')}</MenuHeader>
        <SnoozeChoices ids={ids} onClose={onClose} onDone={onDone} onCustom={() => setCustom(true)} />
      </Popover>
      <SnoozeDialog open={custom} onClose={() => { setCustom(false); onClose() }} onPick={(d) => { setCustom(false); onClose(); snooze(d) }} />
    </>
  )
}

export function SnoozeDialog({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (d: Date) => void }) {
  return <DateTimeDialog open={open} title={t('mail.menus.pickDateTime')} confirmLabel={t('common.save')} onClose={onClose} onPick={onPick} />
}

// ---------- Remind me if no reply ----------

/** Set a follow-up reminder on these conversations (ACT mail.follow_up @web.reader). */
export function useFollowUp(ids: number[]) {
  const { run } = useActions()
  return (d: Date) => {
    void run({ ids, action: 'follow_up', until: d.toISOString() },
      { message: t('mail.menus.ifNobodyRepliesByIt', { optionTime: formatOptionTime(d) }) })
  }
}

/** mail.follow_up: "remind me if nobody replies" by a time. With one set, it can be cancelled.
 * ACT mail.follow_up */
export function FollowUpChoices({ ids, current, onClose, onCustom }: { ids: number[]; current?: string | null; onClose: () => void; onCustom: () => void }) {
  const { run } = useActions()
  const set = useFollowUp(ids)
  return (
    <>
      {followUpOptions().map((o) => (
        <MenuItem key={o.id} icon={<BellRing />} onSelect={() => { onClose(); set(o.at) }} shortcut={formatOptionTime(o.at)}>
          {o.label}
        </MenuItem>
      ))}
      <MenuItem icon={<CalendarClock />} onSelect={onCustom}>
        {t('mail.menus.pickDateTime2')}
      </MenuItem>
      {current && (
        <>
          <MenuSep />
          <MenuItem icon={<BellOff />} onSelect={() => { onClose(); void run({ ids, action: 'cancel_follow_up' }) }}>
            {t('mail.menus.cancelTheReminder')}
          </MenuItem>
        </>
      )}
    </>
  )
}

/** ACT mail.follow_up */
export function FollowUpMenu({ anchor, open, onClose, ids, current }: MenuBase & { current?: string | null }) {
  const [custom, setCustom] = useState(false)
  const set = useFollowUp(ids)
  return (
    <>
      <Popover anchor={anchor} open={open && !custom} onClose={onClose} label={t('mail.menus.remindMeIfNoReply')} minWidth={300}>
        <MenuHeader>{current ? t('mail.menus.reminderSetFor', { optionTime: formatOptionTime(new Date(current)) }) : t('mail.menus.remindMeIfNobodyReplies')}</MenuHeader>
        <FollowUpChoices ids={ids} current={current} onClose={onClose} onCustom={() => setCustom(true)} />
      </Popover>
      <FollowUpDialog open={custom} onClose={() => { setCustom(false); onClose() }} onPick={(d) => { setCustom(false); onClose(); set(d) }} />
    </>
  )
}

export function FollowUpDialog({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (d: Date) => void }) {
  return <DateTimeDialog open={open} title={t('mail.menus.remindMeIfNobodyReplies2')} confirmLabel={t('mail.menus.setReminder')} onClose={onClose} onPick={onPick} />
}

export function DateTimeDialog({ open, title, confirmLabel, onClose, onPick }: { open: boolean; title: string; confirmLabel: string; onClose: () => void; onPick: (d: Date) => void }) {
  const [value, setValue] = useState(() => toLocalInput(new Date(Date.now() + 3600_000)))
  const d = new Date(value)
  const valid = !Number.isNaN(d.getTime()) && d.getTime() > Date.now()
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!valid} onClick={() => onPick(d)}>
            {confirmLabel}
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="dt-pick">{t('mail.menus.dateAndTime')}</label>
        <input id="dt-pick" type="datetime-local" className="input" value={value} min={toLocalInput(new Date())} onChange={(e) => setValue(e.target.value)} />
        {!valid && <span className="error">{t('mail.menus.pickATimeInThe')}</span>}
      </div>
    </Dialog>
  )
}

/** New label. Opened from an account's group (`account`), the label is a folder of that account:
 *  its home, listed there at once and made on that account's server too. */
export function CreateLabelDialog({ open, onClose, onCreated, initialName = '', initialParent = '', account }: { open: boolean; onClose: () => void; onCreated?: (l: Label) => void; initialName?: string; initialParent?: string; account?: MailAccount }) {
  const labels = useLabelList()
  const { create } = useLabelMutations()
  const [name, setName] = useState(initialName)
  const [nest, setNest] = useState(!!initialParent)
  const [parent, setParent] = useState(initialParent)
  const parents = labels.filter((l) => !l.role)
  const full = nest && parent ? `${parent}/${name.trim()}` : name.trim()
  const exists = labels.some((l) => l.name.toLowerCase() === full.toLowerCase())

  const submit = () => {
    if (!full || exists) return
    create.mutate(
      { name: full, ...(account ? { home_account_id: account.id } : {}) },
      {
        onSuccess: (l) => {
          setName('')
          setNest(false)
          onCreated?.(l)
          onClose()
        },
      },
    )
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={account ? t('mail.menus.newFolderIn', { address: account.address }) : t('mail.menus.newLabel3')}
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!full || exists || create.isPending} onClick={submit}>
            {t('common.create')}
          </button>
        </>
      }
    >
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <div className="field">
          <label htmlFor="new-label-name">{t('mail.menus.pleaseEnterANewLabel')}</label>
          <input id="new-label-name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={200} />
          {exists && <span className="error">{t('mail.menus.aLabelWithThisName')}</span>}
          {account && <span className="hint" data-testid="new-folder-account">{t('mail.menus.newFolderHint')}</span>}
        </div>
        <label className="choice">
          <input type="checkbox" className="checkbox" checked={nest} onChange={(e) => setNest(e.target.checked)} />
          <span>{t('mail.menus.nestLabelUnder')}</span>
        </label>
        <select className="select" disabled={!nest} value={parent} onChange={(e) => setParent(e.target.value)} aria-label={t('mail.menus.parentLabel')}>
          <option value="">{t('mail.menus.pleaseSelectAParent')}</option>
          {parents.map((l) => (
            <option key={l.id} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
      </form>
    </Dialog>
  )
}

export { ColorPicker } from './LabelColourPicker'

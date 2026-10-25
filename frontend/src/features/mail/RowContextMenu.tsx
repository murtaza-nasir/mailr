// ACT mail.archive @web.list, ACT mail.move @web.list (Move to Inbox), ACT mail.delete @web.list, ACT mail.read @web.list, ACT mail.unread @web.list, ACT mail.star @web.list, ACT mail.important @web.list, ACT mail.mute @web.list (the row's right-click menu)
// Right-click menu for message-list rows (every theme: MailList catches the event on the list,
// so rows need no code). It acts on the selection when the row is part of it, otherwise on the
// row alone. Snooze, Move to folder, Label as, Show in tab and the rest open as submenus beside the menu, and the
// rarer actions (Category among them) sit under More, so the menu fits a laptop screen without scrolling
// (components/Popover.tsx SubMenu); their lists are the standalone menus' bodies (menus.tsx,
// convMenus.tsx). Dialogs they open (a picked time, a new label, the task and event editors)
// live here, so they outlast the menu.
// ACT mail.snooze @web.list, ACT mail.move @web.list, ACT mail.label @web.list, ACT mail.category @web.list, ACT mail.show_in_tab @web.list, ACT mail.follow_up @web.list (Create from email: CreateFrom.tsx)

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import {
  AlarmClock, Archive, ArchiveRestore, Bot, Copy, Shapes, CornerUpLeft, CornerUpRight, Ellipsis, ExternalLink, Filter, Forward, LayoutList, Mail, MailOpen, Maximize2,
  OctagonAlert, Search, SquarePlus, SquareCheck, Star, StarOff, Tag, Trash2, VolumeX, FolderInput,
} from 'lucide-react'
import { useState, lazy, Suspense } from 'react'
import { useNavigate } from 'react-router'
import { useActions, useConversation } from '../../api/hooks'
import type { ConversationSummary, Message } from '../../api/types'
import { MenuItem, MenuSep, Popover, SubMenu } from '../../components/Popover'
import { findChoices } from '../../lib/convMenus'
import { mailPath } from '../../lib/views'
import { useArchiveSlot, useLabelsById } from '../../lib/rowArchive'
import { toast, useUI } from '../../stores/ui'
// The event and task forms of "Create from message" load when one first opens.
const FromMessageDialog = lazy(() => import('../conversation/CalendarBits').then((m) => ({ default: m.FromMessageDialog })))
import { CreateFromChoices } from '../conversation/CreateFrom'
// "Schedule a meeting" (with the event editor's host) loads when first chosen.
const ScheduleMeetingPicker = lazy(() => import('../conversation/ScheduleMeeting').then((m) => ({ default: m.ScheduleMeetingPicker })))
import { LinkPickerPopover, useLinked } from '../notes/LinkedPanel'
import { CategoryChoices, FollowUpSubMenu, FilterChoices, FindChoices, ShowInTabChoices, useOffersShowInTab } from './convMenus'
import { FollowUpDialog, LabelChoices, MOVE_TO_FOLDER_HINT, MoveChoices, NewLabelDialog, SnoozeChoices, SnoozeDialog, useFollowUp, useSnooze } from './menus'
import { t as tr } from '../../i18n'

export interface RowMenuTarget {
  x: number
  y: number
  items: ConversationSummary[]
}

type Pending =
  | { kind: 'snooze'; ids: number[] }
  | { kind: 'follow_up'; ids: number[] }
  | { kind: 'label'; ids: number[]; name: string }
  | { kind: 'from'; m: Message; what: 'task' | 'event' }
  | { kind: 'meeting' | 'link'; conv: ConversationSummary; at: DOMRect }

export function RowContextMenu({ target, view, q, onClose }: { target: RowMenuTarget | null; view: string; q: string; onClose: () => void }) {
  const nav = useNavigate()
  const { run } = useActions()
  const items = target?.items ?? []
  const ids = items.map((c) => c.id)
  const one = items.length === 1 ? items[0] : null
  const anchor = target ? new DOMRect(target.x, target.y, 0, 0) : null
  const anyUnread = items.some((c) => c.unread_count > 0)
  const allStarred = items.length > 0 && items.every((c) => c.starred)
  const allImportant = items.length > 0 && items.every((c) => c.important)
  const selectedNow = useUI((s) => s.selected)
  const showInTab = useOffersShowInTab()
  // Archive, or Move to Inbox when none of the rows is in the Inbox (lib/rowArchive.ts).
  const slot = useArchiveSlot(items, view, useLabelsById())
  // A dialog a submenu opened, kept after the menu closes (with the ids it acts on).
  const [pending, setPending] = useState<Pending | null>(null)
  const snooze = useSnooze(pending?.kind === 'snooze' ? pending.ids : [])
  const followUp = useFollowUp(pending?.kind === 'follow_up' ? pending.ids : [])

  const close = onClose
  const act = (action: Parameters<typeof run>[0]['action']) => {
    const from = action === 'archive' ? useUI.getState().contextLabelId : null
    void run({ ids, action, ...(from ? { from_label_id: from } : {}) }, { silent: action === 'star' || action === 'unstar' })
    close()
  }
  const openThen = (event?: string) => {
    if (!one) return
    nav(mailPath(view, one.id, q || undefined))
    if (event) setTimeout(() => window.dispatchEvent(new CustomEvent('mailr:reply', { detail: event })), 400)
    close()
  }
  const importantItem = (
    <MenuItem icon={<OctagonAlert />} onSelect={() => act(allImportant ? 'not_important' : 'important')}>
      {allImportant ? tr('mail.rowContextMenu.markNotImportant') : tr('common.markImportant')}
    </MenuItem>
  )
  const later = (p: Pending) => {
    close()
    setPending(p)
  }

  return (
    <>
      <Popover anchor={anchor} open={!!target} onClose={close} label={tr('common.conversationActions')} minWidth={240} className="row-menu">
        {one && (
          <>
            <MenuItem icon={<MailOpen />} onSelect={() => openThen()}>{tr('common.open')}</MenuItem>
            <MenuItem icon={<CornerUpLeft />} onSelect={() => openThen('reply')}>{tr('common.reply')}</MenuItem>
            <MenuItem icon={<CornerUpRight />} onSelect={() => openThen('reply_all')}>{tr('common.replyAll')}</MenuItem>
            <MenuItem icon={<Forward />} onSelect={() => openThen('forward')}>{tr('common.forward')}</MenuItem>
            <MenuSep />
          </>
        )}
        {slot === 'inbox' ? (
          <MenuItem icon={<ArchiveRestore />} onSelect={() => act('inbox')}>{tr('common.moveToInbox')}</MenuItem>
        ) : (
          <MenuItem icon={<Archive />} onSelect={() => act('archive')} shortcut="e">{tr('mail.rowContextMenu.archive', { value: items.length > 1 ? ` ${items.length}` : '' })}</MenuItem>
        )}
        <MenuItem icon={<Trash2 />} onSelect={() => act('trash')} shortcut="#">{tr('common.delete2')}</MenuItem>
        <MenuItem icon={anyUnread ? <Mail /> : <MailOpen />} onSelect={() => act(anyUnread ? 'read' : 'unread')}>
          {anyUnread ? tr('common.markAsRead') : tr('common.markAsUnread')}
        </MenuItem>
        <MenuItem icon={allStarred ? <StarOff /> : <Star />} onSelect={() => act(allStarred ? 'unstar' : 'star')} shortcut="s">
          {allStarred ? tr('common.removeStar') : tr('common.star')}
        </MenuItem>
        {!one && importantItem}
        <SubMenu icon={<AlarmClock />} label={tr('common.snooze')} shortcut="b" minWidth={300}>
          <SnoozeChoices ids={ids} onClose={close} onCustom={() => later({ kind: 'snooze', ids })} />
        </SubMenu>
        <MenuSep />
        <SubMenu icon={<FolderInput />} label={tr('common.moveToFolder')} shortcut="v" minWidth={240} tip={MOVE_TO_FOLDER_HINT}>
          <MoveChoices ids={ids} currentView={view} onClose={close} />
        </SubMenu>
        <SubMenu icon={<Tag />} label={tr('mail.rowContextMenu.labelAs')} shortcut="l" minWidth={260}>
          <LabelChoices ids={ids} items={items} onClose={close} onCreate={(name) => later({ kind: 'label', ids, name })} />
        </SubMenu>
        {showInTab && (
          <SubMenu icon={<LayoutList />} label={tr('common.showInTab')} minWidth={220}>
            <ShowInTabChoices items={items} onClose={close} />
          </SubMenu>
        )}
        <MenuItem icon={<VolumeX />} onSelect={() => act('mute')}>{tr('common.mute')}</MenuItem>
        {one && (
          <>
            <MenuSep />
            <SubMenu icon={<SquarePlus />} label={tr('mail.rowContextMenu.createFromEmail')} minWidth={240}>
              <CreateFromChoices convId={one.id} onClose={close} onFromMessage={(what, m) => setPending({ kind: 'from', m, what })}
                onMeeting={() => anchor && setPending({ kind: 'meeting', conv: one, at: anchor })}
                onLink={() => anchor && setPending({ kind: 'link', conv: one, at: anchor })} />
            </SubMenu>
            <FollowUpSubMenu convId={one.id} onClose={close} onCustom={() => later({ kind: 'follow_up', ids: [one.id] })} />
            {findChoices(one.participants, one.subject).length > 0 && (
              <>
                <SubMenu icon={<Search />} label={tr('mail.rowContextMenu.findMail')} minWidth={240}>
                  <FindChoices participants={one.participants} subject={one.subject} onClose={close} />
                </SubMenu>
                <SubMenu icon={<Filter />} label={tr('mail.rowContextMenu.filterMailLikeThis')} minWidth={240}>
                  <FilterChoices participants={one.participants} subject={one.subject} convId={one.id} onClose={close} />
                </SubMenu>
              </>
            )}
          </>
        )}
        <MenuSep />
        {/* The rarer actions, so the menu stays short. */}
        <SubMenu icon={<Ellipsis />} label={tr('common.more')} minWidth={240}>
          <SubMenu icon={<Shapes />} label={tr('common.category')} minWidth={200}>
            <CategoryChoices items={items} onClose={close} />
          </SubMenu>
          {one && (
            <>
              {importantItem}
              <MenuItem
                icon={<SquareCheck />}
                onSelect={() => {
                  useUI.getState().toggleSelected(one.id)
                  close()
                }}
              >
                {selectedNow.has(one.id) ? tr('common.deselect') : tr('common.select')}
              </MenuItem>
              <MenuItem
                icon={<Bot />}
                onSelect={() => {
                  nav(mailPath(view, one.id, q || undefined))
                  useUI.getState().setPanel('agent')
                  close()
                }}
              >
                {tr('mail.rowContextMenu.askAboutThis', { name: assistantNameNow() })}
              </MenuItem>
              <MenuSep />
              <MenuItem icon={<Maximize2 />} onSelect={() => { nav(`${mailPath(view, one.id, q || undefined)}${q ? '&' : '?'}full=1`); close() }}>
                {tr('mail.rowContextMenu.openInFullView')}
              </MenuItem>
              <MenuItem icon={<ExternalLink />} onSelect={() => { window.open(mailPath(view, one.id), '_blank', 'noopener'); close() }}>
                {tr('mail.rowContextMenu.openInNewWindow')}
              </MenuItem>
              <MenuItem
                icon={<Copy />}
                onSelect={() => {
                  const url = `${window.location.origin}${mailPath(view, one.id)}`
                  void navigator.clipboard?.writeText(url).then(() => toast({ message: tr('common.linkCopied') }), () => toast({ message: url }))
                  close()
                }}
              >
                {tr('common.copyLink')}
              </MenuItem>
            </>
          )}
        </SubMenu>
      </Popover>
      <SnoozeDialog open={pending?.kind === 'snooze'} onClose={() => setPending(null)} onPick={(d) => { setPending(null); snooze(d) }} />
      <FollowUpDialog open={pending?.kind === 'follow_up'} onClose={() => setPending(null)} onPick={(d) => { setPending(null); followUp(d) }} />
      <NewLabelDialog name={pending?.kind === 'label' ? pending.name : null} ids={pending?.kind === 'label' ? pending.ids : []} onClose={() => setPending(null)} />
      {pending?.kind === 'from' && (
        <Suspense fallback={null}>
          <FromMessageDialog m={pending.m} kind={pending.what} onClose={() => setPending(null)} />
        </Suspense>
      )}
      {pending?.kind === 'meeting' && (
        <RowMeeting conv={pending.conv} at={pending.at} onClose={() => setPending(null)} onReply={(text) => {
          nav(mailPath(view, pending.conv.id, q || undefined))
          setTimeout(() => window.dispatchEvent(new CustomEvent('mailr:reply', { detail: { mode: 'reply_all', text } })), 400)
        }} />
      )}
      {pending?.kind === 'link' && <RowLink conv={pending.conv} at={pending.at} onClose={() => setPending(null)} />}
    </>
  )
}

/** Meeting with these people, from the row menu: the conversation's messages, then the same step as the reader's. */
function RowMeeting({ conv, at, onClose, onReply }: { conv: ConversationSummary; at: DOMRect; onClose: () => void; onReply: (text: string) => void }) {
  const messages = useConversation(conv.id).data?.messages ?? []
  // The step stays mounted after it closes: the editor it opened lives in it.
  const [open, setOpen] = useState(true)
  return (
    <Suspense fallback={null}>
      <ScheduleMeetingPicker conv={conv} messages={messages} anchor={open && messages.length ? at : null} onClose={() => setOpen(false)} onReply={(t) => { onReply(t); onClose() }} />
    </Suspense>
  )
}

/** Link to a note or table, from the row menu. */
function RowLink({ conv, at, onClose }: { conv: ConversationSummary; at: DOMRect; onClose: () => void }) {
  const linked = useLinked('conversation', { conversation_id: conv.id }, conv.subject ?? '')
  return <LinkPickerPopover linked={linked} anchor={at} open onClose={onClose} />
}

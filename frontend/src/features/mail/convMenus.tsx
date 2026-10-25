// Submenu bodies shared by the row's right-click menu (RowContextMenu), the reader's ⋮ menu
// (ConversationView) and the selection toolbar's ⋯ menu (ListToolbar): Show in tab, Category,
// Find mail, Filter mail like this and Add to. The pure parts are in lib/convMenus.ts.

import { BellRing, Filter, Search } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { useActions, useActiveLayout, useConversation } from '../../api/hooks'
import type { ConversationSummary, Message, Participant, ShowInTabChoices as Choices, ShowInTabTarget } from '../../api/types'
import { MenuItem, SubMenu } from '../../components/Popover'
import { CATEGORY_TABS, filterChoices, findChoices, offersShowInTab, sharedCategory, shownInText } from '../../lib/convMenus'
import { toast } from '../../stores/ui'
import { layoutIcon } from '../../lib/layoutIcons'
import { mailPath } from '../../lib/views'
import { FollowUpChoices } from './menus'
import { t as tr } from '../../i18n'

/** Whether the active layout has tabs to choose between (Show in tab is left out otherwise). */
export function useOffersShowInTab() {
  const { layout } = useActiveLayout()
  return offersShowInTab(layout)
}

/** The active layout's tabs as Show in tab offers them, and the one these conversations show in
 *  (GET /layouts/show-in-tab, the server's own list rules: backend core/tabplace.py). Fetched when
 *  the submenu opens, so the mark is never stale. */
export function useShowInTabChoices(ids: number[], enabled = true) {
  return useQuery<Choices>({
    queryKey: ['show-in-tab', ...ids],
    queryFn: ({ signal }) => api.get<Choices>('/layouts/show-in-tab', { conversation_ids: ids.join(',') }, signal),
    enabled: enabled && ids.length > 0,
    staleTime: 0,
  })
}

/** Run Show in tab: one server action that adds the tab's label or category and removes what
 *  would take the conversation first, with Undo (core/actions.py show_in_tab). */
export function useShowInTab() {
  const { run } = useActions()
  return (ids: number[], t: ShowInTabTarget) =>
    run({ ids, action: 'show_in_tab', tab: t.id }, { message: shownInText(t.name, ids.length) })
}

/**
 * "Show in tab": exactly the active layout's tabs, in tab-bar order, with one check on the tab the
 * conversations show in now. A tab defined by a search or a state filter is greyed out with the
 * reason (a tooltip, and a toast when tapped). ACT mail.show_in_tab @web.list @web.reader
 */
export function ShowInTabChoices({ items, onClose, onDone }: { items: ConversationSummary[]; onClose: () => void; onDone?: () => void }) {
  const ids = items.map((c) => c.id)
  const q = useShowInTabChoices(ids)
  const show = useShowInTab()
  if (!q.data) return <div className="menu-header">{q.isError ? tr('mail.convMenus.couldNotLoadTheTabs') : tr('common.loading2')}</div>
  return (
    <>
      {q.data.tabs.map((t) => {
        const here = q.data.current === t.id
        const off = t.kind === 'disabled'
        return (
          <MenuItem
            key={t.id}
            icon={layoutIcon(t.icon ?? (t.category ? CATEGORY_TABS[t.category]?.icon : null) ?? 'tag')}
            selected={here}
            dimmed={off}
            tip={t.reason ?? undefined}
            onSelect={() => {
              if (off) {
                toast({ message: t.reason ?? tr('mail.convMenus.mailCannotBePutIn') })
                return
              }
              onClose()
              if (here) return
              void show(ids, t).then(() => onDone?.())
            }}
          >
            {t.name}
          </MenuItem>
        )
      })}
    </>
  )
}

/**
 * "Category" (under More): the raw category, for people who want it whatever the tabs are. One
 * check when every conversation has the same category. The category is the user's correction and
 * trains the category model; Undo puts the earlier one back (core/actions.py, the category action).
 * ACT mail.category @web.list @web.reader
 */
export function CategoryChoices({ items, onClose, onDone }: { items: ConversationSummary[]; onClose: () => void; onDone?: () => void }) {
  const { run } = useActions()
  const ids = items.map((c) => c.id)
  const cur = sharedCategory(items)
  return (
    <>
      {Object.entries(CATEGORY_TABS).map(([id, c]) => (
        <MenuItem
          key={id}
          icon={layoutIcon(c.icon)}
          selected={cur === id}
          onSelect={() => {
            onClose()
            if (cur === id) return
            void run({ ids, action: 'category', category: id }, { message: tr('mail.convMenus.categorySetTo', { itemName: c.name }) }).then(() => onDone?.())
          }}
        >
          {c.name}
        </MenuItem>
      ))}
    </>
  )
}

/** "Find mail": searches built from the conversation (lib/convMenus.ts findChoices). */
export function FindChoices({ participants, subject, onClose }: { participants: Participant[]; subject: string; onClose: () => void }) {
  const nav = useNavigate()
  return (
    <>
      {findChoices(participants, subject).map((c) => (
        <MenuItem key={c.id} icon={<Search />} onSelect={() => { onClose(); nav(mailPath('search', null, c.q)) }}>
          {c.label}
        </MenuItem>
      ))}
    </>
  )
}

/** The newest message's List-Id (a conversation from a mailing list). */
function listIdOf(messages: Message[] | undefined): string | null {
  for (const m of [...(messages ?? [])].reverse()) if (!m.draft && m.list_id) return m.list_id
  return null
}

/** "Filter mail like this": each choice opens the filter editor with its search filled in. The
 *  mailing-list choice shows once the conversation has loaded and has a List-Id. */
export function FilterChoices({ participants, subject, convId, onClose }: { participants: Participant[]; subject: string; convId: number | null; onClose: () => void }) {
  const nav = useNavigate()
  const det = useConversation(convId)
  return (
    <>
      {filterChoices(participants, subject, listIdOf(det.data?.messages)).map((c) => (
        <MenuItem key={c.id} icon={<Filter />} onSelect={() => { onClose(); nav(`/settings/mail/filters?new=${encodeURIComponent(c.q)}`) }}>
          {c.label}
        </MenuItem>
      ))}
    </>
  )
}

/** "Remind me if no reply", once you have written in the conversation (the row menu; the reader has
 * its own button). ACT mail.follow_up @web.list */
export function FollowUpSubMenu({ convId, onClose, onCustom }: { convId: number; onClose: () => void; onCustom: () => void }) {
  const det = useConversation(convId)
  const messages = det.data?.messages ?? []
  const lastMine = [...messages].reverse().find((m) => m.sent_by_me && !m.draft) ?? null
  if (!lastMine) return null
  return (
    <SubMenu icon={<BellRing />} label={tr('mail.convMenus.remindMeIfNoReply')} minWidth={280}>
      <FollowUpChoices ids={[convId]} current={det.data?.conversation.follow_up_at} onClose={onClose} onCustom={onCustom} />
    </SubMenu>
  )
}

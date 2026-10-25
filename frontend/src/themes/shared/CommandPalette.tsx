import { ArrowDown, ArrowUp, BookmarkMinus, BookmarkPlus, Bot, CalendarDays, CircleCheck, Flag, Keyboard, LayoutList, LayoutTemplate, Mail, Moon, Search, Settings, Shapes, Sun, Tag, FolderPlus } from 'lucide-react'
import { useAssistantName } from '../../features/rail/assistant/useAssistantName'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { useActions, useUpdateSettings } from '../../api/hooks'
import { useQueryClient } from '@tanstack/react-query'
import { useTheme } from '../../hooks/useTheme'
import { summariesFor, useMailCommands } from '../../features/mail/commands'
import { useShowInTab, useShowInTabChoices } from '../../features/mail/convMenus'
import { CATEGORY_TABS, sharedCategory } from '../../lib/convMenus'
import { toast } from '../../stores/ui'
import { useMailboxes, useMailRoute } from '../../features/layout/mailboxes'
import { AccountDot, useAccountGroups } from '../../features/layout/accounts'
import { useFavoriteEntries, useFavorites } from '../../features/layout/favorites'
import { canFavorite } from '../../lib/favorites'
import { accountLabelView, labelTree, mailPath } from '../../lib/views'
import type { Label, MailAccount } from '../../api/types'
import { pinsOf } from '../../lib/accountFolders'
import { appHome } from '../../lib/routes'
import { displayKeys } from '../../lib/shortcuts'
import { useUI } from '../../stores/ui'
import { THEME_LIST } from '../registry'
import { useDeviceTheme, useSetTheme, useThemeDef, useThemedSettings } from '../useUITheme'
import { usePalette } from './paletteStore'
import { createHint, createMode, usePaletteItems, type PaletteSection } from '../../features/rail/paletteItems'
import { useRecordingPaletteItems } from '../../features/speakr/palette'
import { useNotePaletteItems } from '../../features/notes/palette'
import { currentAskTarget, openAsk } from '../../features/ask/askStore'
import { askLabel } from '../../features/assistant-context/askAbout'
import { settingsEntries } from '../../features/settings/palette'
import { useVisibility } from '../../features/settings/useVisibility'
import { t as tr, tx } from '../../i18n'


interface Item {
  id: string
  section: 'Favorites' | 'Go to' | 'Accounts' | 'Labels' | 'Mail' | 'App' | 'Layout' | 'Search' | 'Recordings' | 'Notes' | 'Settings' | PaletteSection
  label: string
  hint?: string
  icon: ReactNode
  keys?: string
  /** The quick-add parse under a "New event: …" item. */
  chips?: string[]
  /** Fills the input instead of running and closing ("New event…"). */
  stay?: boolean
  /** Guidance, not a command ("Type the name…"): Enter does nothing, so it shows no ↵. */
  info?: boolean
  /** More words a search matches (a settings section's settings). */
  words?: string
  /** Shown only for a search, never in the list you see before typing ("Settings: …"). */
  searchOnly?: boolean
  run: () => void
}

type Section = Item['section']

/** A section's heading in the chosen language (the section itself is an ID). */
const SECTION_KEYS: Record<Section, string> = {
  Favorites: 'themes.shared.commandPalette.sections.favorites',
  'Go to': 'themes.shared.commandPalette.sections.goTo',
  Accounts: 'themes.shared.commandPalette.sections.accounts',
  Labels: 'themes.shared.commandPalette.sections.labels',
  Mail: 'themes.shared.commandPalette.sections.mail',
  App: 'themes.shared.commandPalette.sections.app',
  Layout: 'themes.shared.commandPalette.sections.layout',
  Search: 'themes.shared.commandPalette.sections.search',
  Recordings: 'themes.shared.commandPalette.sections.recordings',
  Notes: 'themes.shared.commandPalette.sections.notes',
  Settings: 'themes.shared.commandPalette.sections.settings',
  Create: 'themes.shared.commandPalette.sections.create',
  Calendar: 'themes.shared.commandPalette.sections.calendar',
  Tasks: 'themes.shared.commandPalette.sections.tasks',
}
const sectionLabel = (s: Section): string => tr(SECTION_KEYS[s])

function matches(item: Item, words: string[]): boolean {
  const hay = `${item.label} ${item.hint ?? ''} ${sectionLabel(item.section)} ${item.words ?? ''}`.toLowerCase()
  return words.every((w) => hay.includes(w))
}

/** The palette itself; it loads the first time the palette opens (PaletteHost.tsx). */
export function PaletteDialog({ onClose, setNewFolderIn }: { onClose: () => void; setNewFolderIn: (a: MailAccount) => void }) {
  const aName = useAssistantName()
  const nav = useNavigate()
  const [q, setQ] = useState(() => usePalette.getState().initial)
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const { primary, secondary, categories, labels } = useMailboxes()
  const accountGroups = useAccountGroups()
  const favorites = useFavoriteEntries()
  const fav = useFavorites()
  const { commands, targets } = useMailCommands({ advance: true })
  const route = useMailRoute()
  // Show in tab and Category for the selection or the open conversation: one item per choice
  // (ACT mail.show_in_tab @web.palette, ACT mail.category @web.palette).
  const qc = useQueryClient()
  const { run: runAction } = useActions()
  const [actOn] = useState(() => (route.isMail ? targets : []))
  const tabChoices = useShowInTabChoices(actOn)
  const showInTab = useShowInTab()
  const settings = useThemedSettings()
  const folderPins = settings.account_folder_pins
  const settingsVis = useVisibility()
  const upd = useUpdateSettings()
  const colour = useTheme()
  const setTheme = useSetTheme()
  const deviceOnly = useDeviceTheme((s) => s.theme) !== null
  const current = useThemeDef()
  const setPanel = useUI((s) => s.setPanel)
  // Calendar and Tasks: create with the grammar, go to a date, views, calendars, lists and tags.
  const app = usePaletteItems(q, setQ)
  // Speakr recordings: open one, search them, follow-ups (features/speakr/palette.tsx).
  const rec = useRecordingPaletteItems(q, setQ)
  // Notes and checklists: new notes and lists, add to a list, go to a note, search (features/notes/palette.tsx).
  const notesPal = useNotePaletteItems(q, setQ)
  // What Ask would attach: read once, when the palette opens.
  const [askTarget] = useState(currentAskTarget)

  const items = useMemo<Item[]>(() => {
    const go = (view: string) => () => nav(mailPath(view))
    const out: Item[] = []
    // The user's favorites first, with their g 1 … g 9 keys.
    favorites.forEach((e, i) =>
      out.push({ id: `fav:${e.view}`, section: 'Favorites', label: e.tip ?? e.title, icon: e.icon, keys: i < 9 ? `g ${i + 1}` : undefined, hint: e.count ? `${e.count}` : undefined, run: go(e.view) }),
    )
    // The view you are on, as Favorites (Focus has no nav to right-click; the same words as the menus).
    // ACT nav.favorite_add @web.palette, ACT nav.favorite_remove @web.palette, ACT nav.favorite_reorder @web.palette
    const here = route.isMail && !route.q ? route.view : ''
    if (here && canFavorite(here)) {
      const i = fav.keys.indexOf(here)
      if (i < 0) out.push({ id: 'fav:add', section: 'Favorites', label: tr('themes.shared.commandPalette.addToFavorites'), hint: tr('themes.shared.commandPalette.thisView'), icon: <BookmarkPlus />, run: () => fav.add(here) })
      else {
        out.push({ id: 'fav:remove', section: 'Favorites', label: tr('themes.shared.commandPalette.removeFromFavorites'), hint: tr('themes.shared.commandPalette.thisView'), icon: <BookmarkMinus />, run: () => fav.remove(here) })
        if (i > 0) out.push({ id: 'fav:up', section: 'Favorites', label: tr('themes.shared.commandPalette.moveUpInFavorites'), hint: tr('themes.shared.commandPalette.thisView'), icon: <ArrowUp />, run: () => fav.move(here, -1) })
        if (i < fav.keys.length - 1) out.push({ id: 'fav:down', section: 'Favorites', label: tr('themes.shared.commandPalette.moveDownInFavorites'), hint: tr('themes.shared.commandPalette.thisView'), icon: <ArrowDown />, run: () => fav.move(here, 1) })
      }
    }
    for (const e of [...primary, ...secondary, ...categories])
      out.push({ id: `go:${e.view}`, section: 'Go to', label: e.title, icon: e.icon, hint: e.count ? `${e.count}` : undefined, run: go(e.view) })
    // Each account's own folders, while "Show each account separately" is on (Focus has no other nav).
    // ACT mail.account_view @web.palette
    for (const g of accountGroups) {
      for (const e of g.entries)
        out.push({ id: `go:${e.view}`, section: 'Accounts', label: tr('lib.views.accountFolder', { folder: e.title, address: g.account.address }),
          icon: <AccountDot account={g.account} label={false} />, hint: e.count ? `${e.count}` : undefined, run: go(e.view) })
      // The account's folders: labels holding its mail and those made in it (ACT mail.account_label_view @web.palette).
      // Pinned folders first, in the user's order, then the rest (features/layout/folderPins.tsx).
      const pinned = pinsOf(folderPins, g.account.id).map((id) => labels.find((l) => l.id === id && !l.role)).filter((l): l is Label => !!l)
      const ordered = [...pinned, ...labelTree(g.labels).map((x) => x.label).filter((l) => !pinned.includes(l))]
      for (const label of ordered) {
        const v = accountLabelView(g.account.id, label.id)
        const unread = g.views[v]?.unread ?? 0
        out.push({ id: `go:${v}`, section: 'Accounts', label: tr('lib.views.accountFolder', { folder: label.name, address: g.account.address }),
          icon: <AccountDot account={g.account} label={false} />, hint: unread ? tr('themes.shared.commandPalette.unread', { unread }) : undefined, run: go(v) })
      }
      // ACT mail.account_folder_create @web.palette
      out.push({ id: `new-folder:${g.account.id}`, section: 'Accounts', label: tr('layout.accounts.newFolderInAccount', { address: g.account.address }),
        icon: <FolderPlus />, run: () => setNewFolderIn(g.account) })
    }
    for (const { label } of labelTree(labels))
      out.push({ id: `label:${label.id}`, section: 'Labels', label: label.name, icon: <Tag />, hint: label.unread ? tr('themes.shared.commandPalette.unread', { unread: label.unread }) : undefined, run: go(`label:${label.id}`) })
    for (const c of commands) {
      if (!c.enabled || c.id === 'refresh') continue
      const kbMenu = c.id === 'move' || c.id === 'label' || c.id === 'snooze'
      out.push({
        id: `cmd:${c.id}`,
        section: 'Mail',
        label: c.label,
        icon: c.icon,
        keys: c.shortcut,
        run: kbMenu
          ? () => {
              // The same menus the l / v / b keys open.
              const which = c.id as 'move' | 'label' | 'snooze'
              if (route.convId && settings.reading_pane === 'none') window.dispatchEvent(new CustomEvent('mailr:conv-menu', { detail: which }))
              else {
                const ui = useUI.getState()
                if (!ui.selected.size && (route.convId ?? ui.focusedId)) ui.setSelected(new Set([(route.convId ?? ui.focusedId)!]))
                ui.setShortcutMenu(which)
              }
            }
          : () => c.run(null),
      })
    }
    if (actOn.length) {
      const data = tabChoices.data
      if (data && data.tabs.length > 1)
        for (const t of data.tabs)
          out.push({
            id: `tab:${t.id}`, section: 'Mail', label: tr('themes.shared.commandPalette.showInTab', { itemName: t.name }), icon: <LayoutList />,
            hint: data.current === t.id ? tr('themes.shared.commandPalette.showsHereNow') : t.reason ?? undefined,
            run: () => {
              if (t.kind === 'disabled') toast({ message: t.reason ?? tr('themes.shared.commandPalette.mailCannotBePutIn') })
              else if (data.current !== t.id) void showInTab(actOn, t)
            },
          })
      const cur = sharedCategory(summariesFor(qc, actOn))
      for (const [cid, c] of Object.entries(CATEGORY_TABS))
        out.push({
          id: `category:${cid}`, section: 'Mail', label: tr('themes.shared.commandPalette.category', { itemName: c.name }), icon: <Shapes />, hint: cur === cid ? 'current' : undefined,
          run: () => { if (cur !== cid) void runAction({ ids: actOn, action: 'category', category: cid }, { message: tr('themes.shared.commandPalette.categorySetTo', { itemName: c.name }) }) },
        })
    }
    out.push(
      // The apps in the switcher's order: Mail, Calendar, Tasks (Notes: features/notes/palette), Ambrose; then the panels beside.
      { id: 'go:app:mail', section: 'App', label: tr('themes.shared.commandPalette.goToMail'), icon: <Mail />, keys: 'g i', run: () => nav(appHome('mail')) },
      { id: 'go:app:calendar', section: 'App', label: tr('themes.shared.commandPalette.goToCalendar'), icon: <CalendarDays />, keys: 'g c', run: () => nav(appHome('calendar')) },
      { id: 'go:app:tasks', section: 'App', label: tr('themes.shared.commandPalette.goToTasks'), icon: <CircleCheck />, keys: 'g k', run: () => nav(appHome('tasks')) },
      { id: 'app:agent', section: 'App', label: aName, icon: <Bot />, keys: 'ctrl+.', run: () => setPanel('agent') },
      { id: 'app:calendar', section: 'App', label: tr('themes.shared.commandPalette.calendarBeside'), hint: tr('themes.shared.commandPalette.todayAtAGlance'), icon: <CalendarDays />, run: () => setPanel('calendar') },
      { id: 'app:tasks', section: 'App', label: tr('themes.shared.commandPalette.tasksBeside'), hint: tr('themes.shared.commandPalette.openTasksAtAGlance'), icon: <CircleCheck />, run: () => setPanel('tasks') },
      // ACT agent.ask_about @web.palette: the ask capture about what is open (the same as Shift+A).
      askTarget
        ? { id: 'app:ask', section: 'App', label: tr('themes.shared.commandPalette.askAboutThis'), hint: askLabel(askTarget), icon: <Bot />, keys: 'A', run: () => openAsk(askTarget) }
        : { id: 'app:ask', section: 'App', label: tr('themes.shared.commandPalette.ask', { name: aName }), hint: tr('themes.shared.commandPalette.typeOrDictate'), icon: <Bot />, keys: 'A', run: () => openAsk(null) },
      { id: 'app:settings', section: 'App', label: tr('common.settings'), icon: <Settings />, run: () => nav('/settings') },
      // ACT mail.review_importance @web.palette
      { id: 'app:review-importance', section: 'App', label: tr('common.reviewImportance'), hint: tr('themes.shared.commandPalette.confirmOrCorrectWhatThe'), icon: <Flag />, run: () => nav('/settings/ambrose/importance') },
      { id: 'app:help', section: 'App', label: tr('common.keyboardShortcuts'), icon: <Keyboard />, keys: '?', run: () => useUI.getState().setHelp(true) },
      {
        id: 'app:colour',
        section: 'App',
        label: colour === 'dark' ? tr('themes.shared.commandPalette.switchToLightMode') : tr('themes.shared.commandPalette.switchToDarkMode'),
        icon: colour === 'dark' ? <Sun /> : <Moon />,
        run: () => upd.mutate({ theme: colour === 'dark' ? 'light' : 'dark' }),
      },
    )
    for (const t of THEME_LIST)
      if (t.id !== current.id)
        out.push({ id: `theme:${t.id}`, section: 'Layout', label: tr('themes.shared.commandPalette.switchLayoutTo', { itemName: t.name }), hint: t.tagline, icon: <LayoutTemplate />, run: () => setTheme(t.id, deviceOnly) })
    out.push(...app.fixed, ...notesPal.fixed, ...rec.fixed)
    // "Settings: Region" and the rest: every settings section, for a search (features/settings/groups.ts).
    // ACT settings.find @web.palette
    for (const e of settingsEntries(settingsVis, aName))
      out.push({ id: e.id, section: 'Settings', label: e.label, hint: e.hint, words: e.words, searchOnly: true, icon: <Settings />, run: () => nav(e.path) })
    return out
  }, [folderPins, settingsVis, notesPal.fixed, favorites, fav, route.isMail, route.q, route.view, rec.fixed, primary, secondary, categories, accountGroups, labels, commands, nav, setPanel, colour, upd, current.id, setTheme, deviceOnly, route.convId, settings.reading_pane, app.fixed, aName, askTarget, actOn, tabChoices.data, showInTab, qc, runAction, setNewFolderIn])

  const shown = useMemo(() => {
    // "New task: call Dana fri", "Go to 12 Dec": only what answers the command.
    const mode = createMode(q)
    if (mode) return app.dynamic.length ? [...app.dynamic] : [createHint(mode)]
    if (rec.only) return rec.dynamic.length ? [...rec.dynamic] : [rec.hint]
    if (notesPal.only) return [...notesPal.dynamic]
    // "Go to 12 Dec": the date first, so Enter goes there (a notes search only after it).
    if (app.dynamic.some((i) => i.id === 'cal:goto:date')) return [...app.dynamic, ...notesPal.dynamic]
    // "New list: …" offers a checklist (Notes) and a task list.
    if (app.dynamic.some((i) => i.id.startsWith('list:create:'))) return [...notesPal.dynamic, ...app.dynamic]
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean)
    // Names that start with what you typed come first, then names that contain it, then the rest.
    const rank = (i: Item) => {
      const name = i.label.toLowerCase()
      const text = words.join(' ')
      return name.startsWith(text) ? 0 : words.every((w) => name.includes(w)) ? 1 : 2
    }
    const list = words.length
      ? items
          .filter((i) => matches(i, words))
          .map((i, n) => ({ i, n, r: rank(i) }))
          .sort((a, b) => a.r - b.r || a.n - b.n)
          .map((x) => x.i)
      : items.filter((i) => !i.searchOnly)
    if (q.trim())
      list.push({ id: 'search', section: 'Search', label: tr('themes.shared.commandPalette.searchMailFor', { q: q.trim() }), icon: <Search />, run: () => nav(mailPath('search', null, q.trim())) })
    list.push(...notesPal.dynamic, ...app.dynamic, ...rec.dynamic)
    return list
  }, [items, q, nav, app.dynamic, rec.only, rec.dynamic, rec.hint, notesPal.only, notesPal.dynamic])

  useEffect(() => setActive(0), [q])
  useEffect(() => {
    listRef.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const run = (i: Item | undefined) => {
    if (!i) return
    if (i.stay) {
      i.run()
      inputRef.current?.focus()
      return
    }
    onClose()
    // Let the palette unmount first, so menus and focus land on the page.
    setTimeout(i.run, 0)
  }

  let lastSection = ''
  return (
    <div className="scrim palette-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label={tr('themes.shared.commandPalette.commandPalette')}>
        <div className="palette-input">
          <Search aria-hidden />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={tr('themes.shared.commandPalette.goToAViewOr')}
            aria-label={tr('themes.shared.commandPalette.command')}
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={shown[active] ? `pal-${shown[active].id}` : undefined}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActive((a) => Math.min(shown.length - 1, a + 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActive((a) => Math.max(0, a - 1))
              } else if (e.key === 'Enter') {
                e.preventDefault()
                run(shown[active])
              } else if (e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                onClose()
              }
            }}
          />
          <kbd>{tr('common.esc')}</kbd>
        </div>
        <div className="palette-list" id="palette-list" role="listbox" ref={listRef}>
          {shown.length === 0 && <div className="palette-empty">{tr('common.nothingMatches')}</div>}
          {shown.map((i, idx) => {
            const head = i.section !== lastSection ? i.section : null
            lastSection = i.section
            return (
              <div key={i.id}>
                {head && <div className="palette-section">{sectionLabel(head)}</div>}
                <div
                  id={`pal-${i.id}`}
                  data-idx={idx}
                  role="option"
                  aria-selected={idx === active}
                  className={`palette-item${idx === active ? ' active' : ''}`}
                  onMouseMove={() => idx !== active && setActive(idx)}
                  onClick={() => run(i)}
                >
                  <span className="pi-icon">{i.icon}</span>
                  <span className="pi-label">
                    <bdi>{i.label}</bdi>
                    {!!i.chips?.length && (
                      <span className="pi-chips">
                        {i.chips.map((c) => <span key={c} className="pi-chip" dir="auto">{c}</span>)}
                      </span>
                    )}
                  </span>
                  {i.hint && <span className="pi-hint" dir="auto">{i.hint}</span>}
                  {i.keys && <kbd>{displayKeys(i.keys)}</kbd>}
                  {/* ↵ sits on what Enter runs: the selected item (the first one until you move). */}
                  {idx === active && !i.info && <kbd className="pi-enter" title={tr('themes.shared.commandPalette.enterRunsThis')}>↵</kbd>}
                </div>
              </div>
            )
          })}
        </div>
        <div className="palette-foot">
          <span>
            {tx('themes.shared.commandPalette.move', undefined, { kbd: (c) => <kbd>{c}</kbd> })}
          </span>
          <span>
            {tx('themes.shared.commandPalette.run', undefined, { kbd: (c) => <kbd>{c}</kbd> })}
          </span>
          <span>
            {tx('themes.shared.commandPalette.ctrlKToggle', undefined, { kbd: (c) => <kbd>{c}</kbd> })}
          </span>
        </div>
      </div>
    </div>
  )
}

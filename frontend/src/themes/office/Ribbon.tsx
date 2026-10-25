import { useAssistantName } from '../../features/rail/assistant/useAssistantName'
import { Bot, Flag, LayoutTemplate, Monitor, Moon, PanelLeft, Sun } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useSettings, useUpdateSettings } from '../../api/hooks'
import { OverflowMore, type OverflowCommand } from '../../components/OverflowMore'
import { Popover, useAnchor } from '../../components/Popover'
import { useToolbarFit } from '../../hooks/useToolbarFit'
import { currentAskTarget, openAsk } from '../../features/ask/askStore'
import { useCalendarCommands } from '../../features/calendar/commands'
import { groupCommands, type AppCommand, type AppCommands } from '../../features/layout/appCommands'
import { useMailCommands, type MailCommand } from '../../features/mail/commands'
import { useTaskCommands } from '../../features/tasks/commands'
import { useNoteCommands } from '../../features/notes/commands'
import { useUI } from '../../stores/ui'
import { ThemePicker } from '../ThemePicker'
import { useOfficeTabs } from './tabs'
import { t } from '../../i18n'

/** The Home tab's groups. When the ribbon runs out of room (useToolbarFit), groups drop their labels
 *  in `fold` order, then Ask and Compose; past that, groups leave for More in `hide` order. Delete
 *  always stays. */
const GROUPS: { id: string; commands: string[]; fold: number; hide?: number }[] = [
  { id: 'delete', commands: ['delete', 'archive', 'restore', 'spam', 'notSpam'], fold: 4 },
  { id: 'move', commands: ['move', 'label'], fold: 3, hide: 10 },
  { id: 'respond', commands: ['reply', 'replyAll', 'forward'], fold: 2, hide: 8 },
  { id: 'tags', commands: ['read', 'star', 'snooze'], fold: 1, hide: 7 },
]
const ASK_FOLD = 5
const COMPOSE_FOLD = 6
const ASK_HIDE = 9

/** This theme names a star a flag. */
function ribbonLabel(cmd: MailCommand): string {
  if (cmd.id !== 'star') return cmd.label
  return cmd.label === t('common.star') ? t('common.flag') : t('themes.office.ribbon.unflag')
}

function RibbonButton({ cmd, iconOnly }: { cmd: MailCommand; iconOnly?: boolean }) {
  const flag = cmd.id === 'star'
  const label = ribbonLabel(cmd)
  const shortLabel = flag ? label : (cmd.short ?? cmd.label)
  return (
    <button
      type="button"
      className={`rb-btn${iconOnly ? ' icon-only' : ''}`}
      disabled={!cmd.enabled}
      onClick={(e) => cmd.run(e.currentTarget)}
      aria-haspopup={cmd.menu ? 'menu' : undefined}
      data-tip={`${label}${cmd.shortcut ? ` (${cmd.shortcut})` : ''}`}
      aria-label={label}
    >
      {flag ? <Flag /> : cmd.icon}
      <span className="lbl">{shortLabel}</span>
    </button>
  )
}

/**
 * Ask: the ask capture about what is open (the reading pane's conversation, an event's detail,
 * the task pane), as Shift+A, placed under the button. With nothing open it asks without an item.
 * ACT agent.ask_about @web.reader @web.calendar @web.pane
 */
function AskButton({ fold, hide }: { fold?: number; hide?: number }) {
  const name = useAssistantName()
  return (
    <div className="rb-group" data-group="ask" role="group" aria-label={name} data-fold={fold} data-fold-hide={hide}>
      <button type="button" className="rb-btn" data-testid="ribbon-ask" data-tip={t('themes.office.ribbon.askShiftA', { name })} aria-label={t('themes.office.ribbon.ask', { name })}
        onClick={(e) => openAsk(currentAskTarget(), e.currentTarget)}>
        <Bot />
        <span className="lbl">{t('themes.office.ribbon.ask', { name })}</span>
      </button>
    </div>
  )
}

function Segmented<T extends string>({ value, options, onChange, label, iconTips, fold }: { value: T; options: { v: T; label: string; icon?: ReactNode }[]; onChange: (v: T) => void; label: string; iconTips?: boolean; fold?: number }) {
  // iconTips: the options have icons, so they can fold to icons only; the name stays in the tooltip.
  return (
    <div className="rb-seg" role="radiogroup" aria-label={label} data-fold={fold}>
      {options.map((o) => (
        <button key={o.v} type="button" role="radio" aria-checked={value === o.v} className={`rb-btn${value === o.v ? ' on' : ''}`} onClick={() => onChange(o.v)}
          aria-label={iconTips ? o.label : undefined} data-tip={iconTips ? o.label : undefined}>
          {o.icon}
          <span className="lbl">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

/** Office's command bar: a Home tab of labelled mail commands and a View tab of display options. */
export function Ribbon() {
  const [tab, setTab] = useState<'home' | 'view'>('home')
  const unpin = useOfficeTabs((s) => s.unpin)
  const { byId, menus } = useMailCommands({ advance: true, onRemoved: (ids) => ids.forEach(unpin) })
  const compose = byId('compose')!
  const refresh = byId('refresh')!
  const body = useRef<HTMLDivElement>(null)
  const level = useToolbarFit(body)
  const askName = useAssistantName()
  const groups = GROUPS.map((g) => ({ ...g, cmds: g.commands.map(byId).filter((c): c is MailCommand => !!c) }))
  // What left the bar, in the bar's order.
  const gone: OverflowCommand[] = tab !== 'home' ? [] : [
    ...groups.filter((g) => g.hide && level >= g.hide).flatMap((g) => g.cmds.map((c) => ({ ...c, label: ribbonLabel(c), icon: c.id === 'star' ? <Flag /> : c.icon }))),
    ...(level >= ASK_HIDE ? [{ id: 'ask', label: t('themes.office.ribbon.ask', { name: askName }), icon: <Bot />, enabled: true, shortcut: 'Shift+A', run: (a?: HTMLElement | null) => openAsk(currentAskTarget(), a ?? null) }] : []),
  ]

  return (
    <div className="ribbon no-print">
      <div className="rb-tabs" role="tablist" aria-label={t('themes.office.ribbon.ribbon')}>
        <button type="button" role="tab" aria-selected={tab === 'home'} className="rb-tab" onClick={() => setTab('home')}>
          {t('themes.office.ribbon.home')}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'view'} className="rb-tab" onClick={() => setTab('view')}>
          {t('common.view')}
        </button>
      </div>
      <div ref={body} className="rb-body" role="toolbar" aria-label={tab === 'home' ? t('themes.office.ribbon.mailCommands') : t('themes.office.ribbon.viewOptions')}>
        {tab === 'home' ? (
          <>
            <button type="button" className="rb-btn primary" onClick={() => compose.run()} data-tip={t('common.composeC')} aria-label={t('common.compose')} data-fold={COMPOSE_FOLD}>
              {compose.icon}
              <span className="lbl">{t('common.compose')}</span>
            </button>
            {groups.map((g) => (
              <div key={g.id} className="rb-group" data-group={g.id} role="group" aria-label={g.id} data-fold={g.fold} data-fold-hide={g.hide}>
                {g.cmds.map((c) => (
                  <RibbonButton key={c.id} cmd={c} />
                ))}
              </div>
            ))}
            <AskButton fold={ASK_FOLD} hide={ASK_HIDE} />
            <span className="spacer" />
            <OverflowMore commands={gone} className="rb-btn icon-only" />
            <RibbonButton cmd={refresh} iconOnly />
          </>
        ) : (
          <ViewOptions />
        )}
      </div>
      {menus}
    </div>
  )
}

/** The View tab: folder pane, density, colour and layout (the same in every app). */
function ViewOptions() {
  const settings = useSettings()
  const upd = useUpdateSettings()
  const layout = useAnchor()
  const toggleSidebar = useUI((s) => s.toggleSidebar)
  const collapsed = useUI((s) => s.sidebarCollapsed)
  return (
    <>
      {/* Out of room: Layout, Colour and Folder pane drop their labels in turn (density has no icons). */}
      <button type="button" className={`rb-btn${collapsed ? '' : ' on'}`} aria-pressed={!collapsed} onClick={toggleSidebar} aria-label={t('themes.office.ribbon.folderPane')} data-fold={3}>
        <PanelLeft />
        <span className="lbl">{t('themes.office.ribbon.folderPane')}</span>
      </button>
      <div className="rb-group">
        <Segmented
          label={t('themes.office.ribbon.density')}
          value={settings.density}
          onChange={(v) => upd.mutate({ density: v })}
          options={[
            { v: 'compact', label: t('common.compact') },
            { v: 'default', label: t('common.default') },
            { v: 'comfortable', label: t('common.roomy') },
          ]}
        />
      </div>
      <div className="rb-group">
        <Segmented
          label={t('common.colour')}
          value={settings.theme}
          onChange={(v) => upd.mutate({ theme: v })}
          iconTips
          fold={2}
          options={[
            { v: 'system', label: t('themes.office.ribbon.system'), icon: <Monitor /> },
            { v: 'light', label: t('common.light'), icon: <Sun /> },
            { v: 'dark', label: t('common.dark'), icon: <Moon /> },
          ]}
        />
      </div>
      <div className="rb-group" data-fold={1}>
        <button type="button" className="rb-btn" onClick={(e) => layout.toggle(e.currentTarget)} aria-haspopup="dialog" aria-label={t('themes.office.ribbon.layout')}>
          <LayoutTemplate />
          <span className="lbl">{t('themes.office.ribbon.layout')}</span>
        </button>
        <Popover anchor={layout.anchor} open={layout.open} onClose={layout.close} role="dialog" label={t('themes.office.ribbon.layoutAndStyle')} minWidth={340}>
          <div style={{ padding: 12, width: 340 }}>
            <ThemePicker compact />
          </div>
        </Popover>
      </div>
    </>
  )
}

function AppButton({ cmd, fold }: { cmd: AppCommand; fold?: number }) {
  return (
    <button
      type="button"
      className={`rb-btn${cmd.id === 'create' ? ' primary' : ''}${cmd.on ? ' on' : ''}`}
      disabled={!cmd.enabled}
      aria-pressed={cmd.on === undefined ? undefined : cmd.on}
      onClick={(e) => cmd.run(e.currentTarget)}
      aria-haspopup={cmd.menu ? 'menu' : undefined}
      data-tip={`${cmd.label}${cmd.shortcut ? ` (${cmd.shortcut})` : ''}`}
      aria-label={cmd.label}
      data-fold={fold}
    >
      {cmd.icon}
      <span className="lbl">{cmd.short ?? cmd.label}</span>
    </button>
  )
}

/**
 * The ribbon in the Calendar and Tasks apps: a tab of the app's commands (useCalendarCommands /
 * useTaskCommands, the useMailCommands pattern) and the View tab.
 */
export function AppRibbon({ app }: { app: 'calendar' | 'tasks' | 'notes' }) {
  return app === 'calendar' ? <CalendarRibbon /> : app === 'notes' ? <NotesRibbon /> : <TasksRibbon />
}

function CalendarRibbon() {
  return <AppRibbonFrame app="calendar" title={t('common.calendar')} set={useCalendarCommands()} />
}

function NotesRibbon() {
  return <AppRibbonFrame app="notes" title={t('common.notes')} set={useNoteCommands()} />
}

function TasksRibbon() {
  return <AppRibbonFrame app="tasks" title={t('common.tasks')} set={useTaskCommands()} />
}

function AppRibbonFrame({ app, title, set }: { app: string; title: string; set: AppCommands }) {
  const [tab, setTab] = useState<'home' | 'view'>('home')
  const body = useRef<HTMLDivElement>(null)
  const level = useToolbarFit(body)
  const groups = groupCommands(set.commands)
  // Out of room: the last group drops its labels first, Ask and the Create button last; then the
  // groups after the first leave for More, the last first.
  const n = groups.length
  const fold = (i: number) => n - i
  const askFold = n + 1
  const createFold = n + 2
  const hide = (i: number) => (i === 0 ? undefined : createFold + (n - i))
  const gone: OverflowCommand[] = tab !== 'home' ? [] : groups.flatMap((g, i) => (hide(i) !== undefined && level >= hide(i)! ? g.commands : []))
  return (
    <div className="ribbon no-print" data-ribbon={app}>
      <div className="rb-tabs" role="tablist" aria-label={t('themes.office.ribbon.ribbon')}>
        <button type="button" role="tab" aria-selected={tab === 'home'} className="rb-tab" onClick={() => setTab('home')}>
          {title}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'view'} className="rb-tab" onClick={() => setTab('view')}>
          {t('common.view')}
        </button>
      </div>
      <div ref={body} className="rb-body" role="toolbar" aria-label={tab === 'home' ? t('themes.office.ribbon.commands', { title }) : t('themes.office.ribbon.viewOptions')}>
        {tab === 'home' ? (
          <>
            {groups.map((g, i) => (
              <div key={g.group} className="rb-group" data-group={g.group} role="group" aria-label={g.group} data-fold={fold(i)} data-fold-hide={hide(i)}>
                {g.commands.map((c) => (
                  <AppButton key={c.id} cmd={c} fold={c.id === 'create' ? createFold : undefined} />
                ))}
              </div>
            ))}
            <AskButton fold={askFold} />
            <OverflowMore commands={gone} className="rb-btn icon-only" />
          </>
        ) : (
          <ViewOptions />
        )}
      </div>
      {set.menus}
    </div>
  )
}

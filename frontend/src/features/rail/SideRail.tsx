import { Bot, CalendarDays, CircleCheck, StickyNote, X } from 'lucide-react'
import { appTitle, useAssistantName } from './assistant/useAssistantName'
import { lazy, Suspense, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router'
import { ErrorBoundary, Spinner } from '../../components/bits'

// The assistant loads when its panel first opens (its Markdown and chat code are not needed before).
const AssistantPanel = lazy(() => import('./assistant/AssistantPanel').then((m) => ({ default: m.AssistantPanel })))
import { convFromSlug } from '../../lib/pubid'
import { appHome } from '../../lib/routes'
import { useUI, type RailPanel } from '../../stores/ui'
import './assistant/assistant.css'
// The beside panels load when one first opens: the calendar panel alone brings the event detail,
// editor and calendar list (about 100 kB), which the first inbox paint does not need.
const CalendarPanel = lazy(() => import('./CalendarPanel').then((m) => ({ default: m.CalendarPanel })))
const TasksPanel = lazy(() => import('./TasksPanel').then((m) => ({ default: m.TasksPanel })))
const NotesPanel = lazy(() => import('./NotesPanel').then((m) => ({ default: m.NotesPanel })))
import { t } from '../../i18n'
import { assistantNameNow } from './assistant/assistantName'

const PANELS: { id: Exclude<RailPanel, null>; title: string; icon: ReactNode }[] = [
  { id: 'calendar', title: t('common.calendar'), icon: <CalendarDays /> },
  { id: 'tasks', title: t('common.tasks'), icon: <CircleCheck /> },
  { id: 'notes', title: t('common.notes'), icon: <StickyNote /> },
  { id: 'agent', get title() { return t('rail.sideRail.text', { name: assistantNameNow() }) }, icon: <Bot /> },
]

/** The apps that can open beside the page (Classic's right rail, the palette). */
export const BESIDE_PANELS = PANELS.filter((p) => p.id !== 'agent') as { id: 'calendar' | 'tasks' | 'notes'; title: string; icon: ReactNode }[]

/**
 * Classic's right rail: "Open beside" toggles, one per app. They open a small panel of that
 * app beside the page; they are not a second app switcher (the sidebar's Apps group is), so each toggle
 * is named "<App> beside" and its tooltip says "Open <App> beside the page". The rail has no caption
 *. Ambrose is the top bar's button (AmbroseButton).
 */
export function SideRail() {
  const panel = useUI((s) => s.panel)
  const setPanel = useUI((s) => s.setPanel)
  return (
    <aside className="rail no-print" aria-label={t('rail.sideRail.openBeside')}>
      {BESIDE_PANELS.map((p) => (
        <button key={p.id} type="button" className={`icon-btn rail-beside${panel === p.id ? ' active' : ''}`} aria-label={t('rail.sideRail.beside', { title: p.title })} aria-pressed={panel === p.id}
          data-tip={panel === p.id ? t('rail.sideRail.closeBeside', { title: p.title }) : t('rail.sideRail.openBesideThePage', { title: p.title })} data-beside={p.id} onClick={() => setPanel(panel === p.id ? null : p.id)}>
          {p.icon}
        </button>
      ))}
    </aside>
  )
}

export function SidePanel() {
  const aName = useAssistantName()
  const open = useUI((s) => s.panel)
  const away = useUI((s) => s.ambroseAway)
  const setPanel = useUI((s) => s.setPanel)
  const location = useLocation()
  // Ambrose stepped aside for an item opened from the chat (phone layout, ambroseNav.ts): the chat
  // stays mounted and hidden, so it comes back as it was (scroll place, a reply still arriving).
  // The same tree in both states, so React keeps the chat when the panel opens again.
  const parked = !open && away
  const panel = open ?? (parked ? 'agent' : null)
  if (!panel) return null
  const def = PANELS.find((p) => p.id === panel)!
  // The conversation open in the main view, for assistant context.
  const m = /^\/mail\/[^/]+\/([0-9A-Za-z]+)/.exec(location.pathname)
  const conversationId = m ? convFromSlug(m[1]) : null
  return (
    // data-keep-active: a modal over the page (the note editor) leaves this panel usable beside it.
    <aside className={parked ? 'side-panel-parked' : 'side-panel no-print'} hidden={parked} aria-label={appTitle(def, aName)} data-keep-active>
      <div className="side-panel-head">
        <h2>{appTitle(def, aName)}</h2>
        {/* A panel's own title beside its name (the open Ambrose chat's title). */}
        <div className="side-panel-title" id="side-panel-title" />
        {/* Panels put their own header actions here (e.g. the assistant's "New chat"). */}
        <div className="side-panel-actions" id="side-panel-actions" />
        {/* The panels are quick glances; the full apps are one click away. */}
        {(panel === 'calendar' || panel === 'tasks' || panel === 'notes') && (
          <Link className="btn text sm side-panel-open" to={appHome(panel)} onClick={() => setPanel(null)} data-open-app={panel}>
            {panel === 'calendar' ? t('rail.sideRail.openCalendar') : panel === 'notes' ? t('rail.sideRail.openNotes') : t('rail.sideRail.openTasks')}
          </Link>
        )}
        <button type="button" className="icon-btn sm" aria-label={t('rail.sideRail.closePanel')} data-tip={t('common.close')} onClick={() => setPanel(null)}>
          <X size={20} />
        </button>
      </div>
      <div className="side-panel-body">
        <ErrorBoundary>
          <Suspense fallback={<div style={{ display: 'grid', placeItems: 'center', padding: 32 }}><Spinner /></div>}>
            {panel === 'calendar' && <CalendarPanel />}
            {panel === 'tasks' && <TasksPanel />}
            {panel === 'notes' && <NotesPanel />}
            {panel === 'agent' && <AssistantPanel conversationId={conversationId} />}
          </Suspense>
        </ErrorBoundary>
      </div>
    </aside>
  )
}

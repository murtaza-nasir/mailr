// The app's routes and the hook that reads them. Mail, Calendar, Tasks, Notes, Recordings and Settings share one
// frame (AppShell → the theme's shell); the URL decides which app the main region shows.
// Path parsing and building live in lib/routes.ts (tested); this file draws the routes.
//
// Calendar, Tasks and Recordings each have one splat route, so the app stays mounted while its URL changes
// (week → event detail → next week) and keeps its state.

import { lazy, Suspense, useEffect, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router'
import { Spinner } from './components/bits'
import { Toasts } from './components/Toasts'
import { parseApprovalId, requestApprovalFocus } from './features/approvals/focus'
import { LoginPage } from './features/auth/LoginPage'
import { AppShell } from './features/layout/AppShell'
import { MailPage } from './features/mail/MailPage'
import { ComposeLink } from './features/compose/ComposeLink'
import { notesPath, parseTablePath } from './lib/routes'

// The hook and the path helpers, for one import site (the apps may also import them directly
// from hooks/useAppRoute and lib/routes, which keeps their tests free of the whole app).
export { useAppRoute, type AppRoute } from './hooks/useAppRoute'
export * from './lib/routes'

const SettingsPage = lazy(() => import('./features/settings/SettingsPage'))
const CalendarApp = lazy(() => import('./features/calendar/CalendarApp'))
const TasksApp = lazy(() => import('./features/tasks/TasksApp'))
const RecordingsApp = lazy(() => import('./features/recordings/RecordingsApp'))
const NotesApp = lazy(() => import('./features/notes/NotesApp'))
const TableApp = lazy(() => import('./features/tables/TableApp'))
const SetupGuide = lazy(() => import('./features/onboarding/SetupGuide'))
const JoinPage = lazy(() => import('./features/auth/JoinPage').then((m) => ({ default: m.JoinPage })))
const GetAppPage = lazy(() => import('./features/onboarding/GetAppPage').then((m) => ({ default: m.GetAppPage })))

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div style={{ padding: 48, display: 'grid', placeItems: 'center' }}>
          <Spinner />
        </div>
      }
    >
      {children}
    </Suspense>
  )
}

/** /approvals[/:id]: the link agents and notifications hand out. It opens the approvals drawer
 * (on that approval) over the inbox, in every theme (the "Waiting for you" bell, WaitingBell). */
function ApprovalLink() {
  const { id } = useParams()
  const navigate = useNavigate()
  useEffect(() => {
    const parsed = parseApprovalId(id)
    if (parsed === undefined) requestApprovalFocus(null, id ?? '')
    else requestApprovalFocus(parsed)
    navigate('/mail/inbox', { replace: true })
  }, [id, navigate])
  return null
}

/** One table, or (for /tables and anything else under it) the Tables view of Notes. */
function TablesRoute() {
  const { pathname } = useLocation()
  if (!parseTablePath(pathname)) return <Navigate to={notesPath('tables')} replace />
  return <Lazy><TableApp /></Lazy>
}

export function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <>
            <LoginPage />
            <Toasts />
          </>
        }
      />
      {/* The page a phone opens from the setup guide's QR code (no sign-in). */}
      <Route path="/get-app" element={<Lazy><GetAppPage /></Lazy>} />
      {/* A household invite (Admin → Users → Invite someone); no sign-in needed to open it. */}
      <Route path="/join/:token" element={<Lazy><JoinPage /></Lazy>} />
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/mail/inbox" replace />} />
        {/* The setup guide, full screen (AppShell draws it without the theme's frame). */}
        <Route path="/setup/:step?" element={<Lazy><SetupGuide /></Lazy>} />
        {/* /compose?to=…&subject=…&body=… or ?mailto=<mailto: URL>: a prefilled draft (email links, compose.deeplink) */}
        <Route path="/compose" element={<ComposeLink />} />
        <Route path="/mail/:view" element={<MailPage />} />
        <Route path="/mail/:view/:convId" element={<MailPage />} />
        {/* /calendar[/:view[/:date]], /calendar/event/:uid/:occ, /calendar/search?q= */}
        <Route path="/calendar/*" element={<Lazy><CalendarApp /></Lazy>} />
        {/* /tasks[/:list], /tasks[/:list]/task/:uid */}
        <Route path="/tasks/*" element={<Lazy><TasksApp /></Lazy>} />
        {/* /approvals[/:id]: approval links from agents, MCP and push notifications */}
        <Route path="/approvals" element={<ApprovalLink />} />
        <Route path="/approvals/:id" element={<ApprovalLink />} />
        <Route path="/settings/:tab?/:page?" element={<Lazy><SettingsPage /></Lazy>} />
        {/* /recordings[/<filter>], /recordings/:cid/:rid?t=<seconds>: Speakr recordings (web.recordings) */}
        <Route path="/recordings/*" element={<Lazy><RecordingsApp /></Lazy>} />
        {/* /notes[/<view>], /notes/label/<name>, /notes/search?q=, /notes/n/<id>[#i-<item>] (web.notes, web.note) */}
        <Route path="/notes/*" element={<Lazy><NotesApp /></Lazy>} />
        {/* /tables/t/<id>[/v/<view>][/r/<row>]: one table, part of the Notes app (web.table).
            Tables live inside Notes (Notes · Lists · Tables): /tables itself opens Notes → Tables. */}
        <Route path="/tables/*" element={<TablesRoute />} />
        <Route path="*" element={<Navigate to="/mail/inbox" replace />} />
      </Route>
    </Routes>
  )
}

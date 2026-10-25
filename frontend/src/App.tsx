import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect } from 'react'
import { BrowserRouter, useLocation, useNavigate } from 'react-router'
import { ApiError, request, setUnauthorizedHandler } from './api/client'
import { linkDerivedCounts } from './api/hooks'
import { flushAllDrafts } from './lib/draftStore'
import { forgetDeviceData } from './lib/serviceWorker'
import { classifyError } from './lib/netErrors'
import { useCompose } from './stores/compose'
import { loginNext } from './features/compose/ComposeLink'
import { AppRoutes } from './router'
import './styles/lazyFeatureStyles'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retry only what can succeed on a second try: network failures and gateway errors.
      // A 4xx/500 answer is deterministic, and retrying it keeps stale data on screen.
      retry: (count, err) => {
        const status = (err as ApiError).status
        if (status && ![502, 503, 504].includes(status)) return false
        return count < 2
      },
      refetchOnWindowFocus: true,
    },
  },
})

linkDerivedCounts(queryClient)

/** Pages that need no sign-in: a 401 there is expected (the invite page reads its own facts). */
const PUBLIC = /^\/(login|join\/|get-app)/

function UnauthorizedRedirect() {
  const nav = useNavigate()
  const location = useLocation()
  useEffect(() => {
    let checking = false
    const signOut = () => {
      if (PUBLIC.test(window.location.pathname)) return
      // Keep what is being written: the local copies reopen after signing in again.
      flushAllDrafts(true)
      useCompose.getState().reset()
      queryClient.clear()
      // The session ended: mail kept for offline reading goes (the offline queue waits for sign-in).
      forgetDeviceData()
      nav(`/login?next=${encodeURIComponent(loginNext(window.location.pathname + window.location.search))}`, { replace: true })
    }
    setUnauthorizedHandler((confirmed) => {
      if (PUBLIC.test(window.location.pathname)) return
      if (confirmed) return signOut()
      // One request's 401 is not proof: ask the session check, and sign out only when it says so.
      // A network error or a 5xx from it keeps the app.
      if (checking) return
      checking = true
      request<{ authenticated: boolean }>('/auth/session', { quiet401: true, timeoutMs: 15_000 })
        .then((r) => {
          if (!r?.authenticated) signOut()
        })
        .catch((e) => {
          if (classifyError(e) === 'unauthorized') signOut()
        })
        .finally(() => {
          checking = false
        })
    })
  }, [nav, location])
  return null
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <UnauthorizedRedirect />
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}

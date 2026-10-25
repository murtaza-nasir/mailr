// The reader, loaded apart from the first page. The inbox paints without it (with its summary, notes,
// calendar and task pieces it is about a fifth of the app's first-load code); it loads in the
// background once the page is idle (preloadReader), so opening a conversation does not wait for it,
// and at once when the address already names a conversation.

import { lazy, Suspense, type ComponentProps } from 'react'
import { Skeleton } from '../../components/bits'
import type { ConversationView } from './ConversationView'

const load = () => import('./ConversationView')
const View = lazy(() => load().then((m) => ({ default: m.ConversationView })))

let preloaded = false
/** Fetch the reader's code when the browser is idle (once). */
export function preloadReader() {
  if (preloaded) return
  preloaded = true
  const go = () => void load().catch(() => { preloaded = false })
  if (typeof window !== 'undefined' && 'requestIdleCallback' in window) window.requestIdleCallback(go, { timeout: 3000 })
  else setTimeout(go, 1500)
}

/** The reader's loading state: its own while the conversation loads, and while its code does. */
export function ConversationSkeleton() {
  return (
    <div className="conv-view">
      <div className="conv-view-head" />
      <div className="conv-loading">
        <Skeleton width="50%" height={24} />
        <div style={{ display: 'flex', gap: 16, marginTop: 32 }}>
          <Skeleton width={40} height={40} style={{ borderRadius: '50%' }} />
          <div style={{ flex: 1 }}>
            <Skeleton width="30%" />
            <Skeleton width="80%" style={{ marginTop: 16 }} />
            <Skeleton width="70%" style={{ marginTop: 8 }} />
          </div>
        </div>
      </div>
    </div>
  )
}

export function LazyConversationView(props: ComponentProps<typeof ConversationView>) {
  return (
    <Suspense fallback={<ConversationSkeleton />}>
      <View {...props} />
    </Suspense>
  )
}

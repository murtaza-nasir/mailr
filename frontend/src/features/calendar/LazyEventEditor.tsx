// The event editor for hosts outside the Calendar app (the reader's "Schedule a meeting", the side
// panel's overlays). Its code (the editor, its fields, Find a time) loads when an editor first opens,
// not with the first page. The Calendar app imports the editor directly.

import { lazy, Suspense, type ComponentProps } from 'react'
import type { EventEditor } from './EventEditor'

const Editor = lazy(() => import('./EventEditor').then((m) => ({ default: m.EventEditor })))

export function LazyEventEditor(props: ComponentProps<typeof EventEditor>) {
  return (
    <Suspense fallback={null}>
      <Editor {...props} />
    </Suspense>
  )
}

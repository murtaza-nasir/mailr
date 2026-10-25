// The compose form loaded when a compose window or an inline reply first opens: the editor
// (Tiptap and ProseMirror) is the largest library mailr has and the inbox does not need it.
import { lazy, Suspense } from 'react'
import { Spinner } from '../../components/bits'
import type { ComposeFormProps } from './ComposeForm'

const Impl = lazy(() => import('./ComposeForm').then((m) => ({ default: m.ComposeForm })))

export function ComposeForm(props: ComposeFormProps) {
  return (
    <Suspense
      fallback={
        <div style={{ display: 'grid', placeItems: 'center', minHeight: 160 }}>
          <Spinner />
        </div>
      }
    >
      <Impl {...props} />
    </Suspense>
  )
}

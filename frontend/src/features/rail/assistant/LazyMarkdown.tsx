// Markdown (Markdown.tsx) loaded on first use: react-markdown and remark-gfm stay out of the
// first page load (the inbox needs neither). Until it arrives the text shows as plain lines.
import { lazy, Suspense, type ComponentProps } from 'react'

const Impl = lazy(() => import('./Markdown').then((m) => ({ default: m.Markdown })))

export function Markdown(props: ComponentProps<typeof Impl>) {
  return (
    <Suspense fallback={<div className="md-loading" style={{ whiteSpace: 'pre-wrap' }}>{props.text}</div>}>
      <Impl {...props} />
    </Suspense>
  )
}

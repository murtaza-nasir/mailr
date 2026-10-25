// A recording's summary or notes as GitHub-flavoured Markdown, without raw HTML or images (they
// show as a note). Loaded lazily (LazyGfmText), so react-markdown stays out of the first page load.
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ComponentType } from 'react'
import { rehypeDirAuto } from '../../lib/rehypeDirAuto'

export default function GfmText({ text, img }: { text: string; img: ComponentType<{ alt?: string; src?: string }> }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeDirAuto]} skipHtml components={{ img }}>
      {text}
    </ReactMarkdown>
  )
}

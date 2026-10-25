// Rendered Markdown (Ambrose's answers, task notes, recording notes): every block takes the direction
// of its own text (dir="auto"), so an Arabic or Hebrew paragraph reads right to left, and a list in
// Arabic has its bullets on the right, while the English blocks around it stay left to right. Code
// keeps its direction. A rehype plugin, as Speakr's md_to_html does it (#414).

interface HastNode {
  type: string
  tagName?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

const BLOCKS = new Set(['p', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'td', 'th', 'dt', 'dd'])

function walk(n: HastNode): void {
  if (n.type === 'element' && n.tagName && BLOCKS.has(n.tagName)) {
    n.properties ??= {}
    if (!n.properties.dir) n.properties.dir = 'auto'
  }
  for (const c of n.children ?? []) walk(c)
}

/** The plugin: `<ReactMarkdown rehypePlugins={[rehypeDirAuto]}>`. */
export function rehypeDirAuto() {
  return (tree: HastNode) => walk(tree)
}

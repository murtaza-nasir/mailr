import { EditorContent, useEditor, type Editor as TiptapEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import TextAlign from '@tiptap/extension-text-align'
import { Color, TextStyle } from '@tiptap/extension-text-style'
import { NodeSelection } from '@tiptap/pm/state'
import {
  AlignCenter, AlignLeft, AlignRight, Bold, Eraser, Italic, Link2, List, ListOrdered, Palette, Quote, Redo2, Strikethrough, Underline, Undo2,
} from 'lucide-react'
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import { Popover, useAnchor } from '../../components/Popover'
import { INLINE_TYPES, stepSize } from './inlineImages'
import { MailImage } from './mailImage'
import { asEditorHtml } from './composeText'
import { t } from '../../i18n'
import { isRtl } from '../../lib/rtl'

// The alignment buttons: physical sides of the mail's paragraphs, as the sender chooses them.
const ALIGN: [string, string, ReactNode][] = [
  ['left', t('compose.editor.alignLeft'), <AlignLeft key="l" />],
  ['center', t('compose.editor.alignCentre'), <AlignCenter key="c" />],
  ['right', t('compose.editor.alignRight'), <AlignRight key="r" />],
]

const TEXT_COLORS = ['#000000', '#444746', '#999999', '#d93025', '#e37400', '#f9ab00', '#188038', '#1a73e8', '#9334e6', '#c5221f', '#ffffff']

interface EditorProps {
  html: string
  onChange: (html: string) => void
  placeholder?: string
  autoFocus?: boolean
  onReady?: (editor: TiptapEditor) => void
  onSubmit?: () => void
  onPasteFiles?: (files: File[]) => void
  /** Image files pasted or dropped onto the body go in at the cursor (or the drop point). */
  onInsertImages?: (files: File[], pos?: number) => void
  /** Runs first on every key press; true = handled (compose uses it for `;shortcut` templates). */
  onKeyDown?: (editor: TiptapEditor, event: KeyboardEvent) => boolean
}

/** Pasted or dropped files: images that can go into the body, and everything else. */
function splitFiles(files: File[]): { images: File[]; others: File[] } {
  const images = files.filter((f) => (INLINE_TYPES as readonly string[]).includes(f.type))
  return { images, others: files.filter((f) => !images.includes(f)) }
}

export function useMailEditor({ html, onChange, placeholder, autoFocus, onSubmit, onPasteFiles, onInsertImages, onKeyDown }: EditorProps) {
  const onChangeRef = useRef(onChange)
  const onSubmitRef = useRef(onSubmit)
  const onPasteRef = useRef(onPasteFiles)
  const onImagesRef = useRef(onInsertImages)
  const onKeyRef = useRef(onKeyDown)
  const editorRef = useRef<TiptapEditor | null>(null)
  useEffect(() => {
    onChangeRef.current = onChange
    onSubmitRef.current = onSubmit
    onPasteRef.current = onPasteFiles
    onImagesRef.current = onInsertImages
    onKeyRef.current = onKeyDown
  })
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        link: { openOnClick: false, autolink: true, HTMLAttributes: { rel: t('compose.editor.noopenerNoreferrer'), target: '_blank' } },
        heading: false,
        codeBlock: false,
      }),
      TextStyle,
      Color,
      TextAlign.configure({ types: ['paragraph'] }),
      MailImage.configure({ inline: true, allowBase64: true }),
      Placeholder.configure({ placeholder: placeholder ?? '' }),
    ],
    content: html ? asEditorHtml(html) : '',
    // Every paragraph takes the direction of its first letter (dir="auto"), in the editor and in the
    // mail that is sent, so Arabic or Hebrew reads right to left for the recipient too.
    textDirection: 'auto',
    autofocus: autoFocus ? 'start' : false,
    onUpdate: ({ editor }) => onChangeRef.current(editor.isEmpty ? '' : editor.getHTML()),
    editorProps: {
      attributes: { 'aria-label': t('compose.editor.messageBody'), role: 'textbox', 'aria-multiline': 'true' },
      handleKeyDown: (view, event) => {
        if (editorRef.current && onKeyRef.current?.(editorRef.current, event)) return true
        // ACT mail.insert_image @web.editor: with an image selected, you resize it by a step with - and +.
        const sel = view.state.selection
        const ed = editorRef.current
        if (ed && sel instanceof NodeSelection && sel.node.type.name === 'image' && !event.ctrlKey && !event.metaKey && !event.altKey && ['-', '+', '='].includes(event.key)) {
          const img = (view.nodeDOM(sel.from) as HTMLElement | null)?.querySelector('img')
          const width = sel.node.attrs.width || img?.getBoundingClientRect().width || 0
          if (width) {
            event.preventDefault()
            const natural = { width: img?.naturalWidth || width, height: img?.naturalHeight || sel.node.attrs.height || width }
            ed.chain().setImageSize(stepSize(width, natural, event.key === '-' ? -1 : 1, (view.dom as HTMLElement).clientWidth)).setNodeSelection(sel.from).run()
            return true
          }
        }
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
          event.preventDefault()
          onSubmitRef.current?.()
          return true
        }
        return false
      },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files ?? [])
        if (!files.length) return false
        const { images, others } = onImagesRef.current ? splitFiles(files) : { images: [], others: files }
        if (!onPasteRef.current && !images.length) return false
        event.preventDefault()
        if (images.length) onImagesRef.current?.(images)
        if (others.length) onPasteRef.current?.(others)
        return true
      },
      // Files dropped onto the body: images go in where they are dropped, other files are
      // attached. (Dropped anywhere else in the window, every file is attached.)
      handleDrop: (view, event, _slice, moved) => {
        const files = Array.from(event.dataTransfer?.files ?? [])
        if (moved || !files.length || !onImagesRef.current) return false
        event.preventDefault()
        event.stopPropagation()
        const { images, others } = splitFiles(files)
        const pos = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos
        if (images.length) onImagesRef.current(images, pos)
        if (others.length) onPasteRef.current?.(others)
        return true
      },
    },
  })
  useEffect(() => {
    editorRef.current = editor
  }, [editor])
  return editor
}

export function EditorArea({ editor, children, dropInline }: { editor: TiptapEditor | null; children?: ReactNode; dropInline?: boolean }) {
  return (
    <div className={`editor-wrap${dropInline ? ' drop-inline' : ''}`} onClick={(e) => {
      if ((e.target as HTMLElement).closest('.img-toolbar')) return
      editor?.chain().focus().run()
    }}>
      <EditorContent editor={editor} />
      {children}
    </div>
  )
}

export function FormatBar({ editor }: { editor: TiptapEditor | null }) {
  const colorMenu = useAnchor()
  const linkMenu = useAnchor()
  const [href, setHref] = useState('')
  // Re-render on selection change so active states update.
  const [, force] = useState(0)
  useEffect(() => {
    if (!editor) return
    const f = () => force((n) => n + 1)
    editor.on('selectionUpdate', f)
    editor.on('transaction', f)
    return () => {
      editor.off('selectionUpdate', f)
      editor.off('transaction', f)
    }
  }, [editor])
  if (!editor) return null
  const btn = (label: string, icon: ReactNode, onClick: () => void, active = false, disabled = false) => (
    <button type="button" className={`icon-btn${active ? ' active' : ''}`} aria-label={label} data-tip={label} aria-pressed={active} disabled={disabled} onMouseDown={(e) => e.preventDefault()} onClick={onClick}>
      {icon}
    </button>
  )
  const c = () => editor.chain().focus()
  return (
    <div className="fmt-bar" role="toolbar" aria-label={t('compose.editor.formattingOptions')}>
      {btn(t('compose.editor.undoCtrlZ'), <Undo2 />, () => c().undo().run(), false, !editor.can().undo())}
      {btn(t('compose.editor.redoCtrlY'), <Redo2 />, () => c().redo().run(), false, !editor.can().redo())}
      <span className="sep" />
      {btn(t('compose.editor.boldCtrlB'), <Bold />, () => c().toggleBold().run(), editor.isActive('bold'))}
      {btn(t('compose.editor.italicCtrlI'), <Italic />, () => c().toggleItalic().run(), editor.isActive('italic'))}
      {btn(t('compose.editor.underlineCtrlU'), <Underline />, () => c().toggleUnderline().run(), editor.isActive('underline'))}
      {btn(t('compose.editor.strikethrough'), <Strikethrough />, () => c().toggleStrike().run(), editor.isActive('strike'))}
      <button type="button" className="icon-btn" aria-label={t('compose.editor.textColour')} data-tip={t('compose.editor.textColour')} onMouseDown={(e) => e.preventDefault()} onClick={(e) => colorMenu.toggle(e.currentTarget)}>
        <Palette />
      </button>
      <span className="sep" />
      {/* Left, centre and right are the sides of the page, in this order on the screen in either
          direction (the toolbar runs right to left in a right-to-left page). */}
      {(isRtl() ? ALIGN.slice().reverse() : ALIGN).map(([side, label, icon]) => (
        <Fragment key={side}>{btn(label, icon, () => c().setTextAlign(side).run(), editor.isActive({ textAlign: side }))}</Fragment>
      ))}
      {btn(t('compose.editor.numberedList'), <ListOrdered />, () => c().toggleOrderedList().run(), editor.isActive('orderedList'))}
      {btn(t('compose.editor.bulletedList'), <List />, () => c().toggleBulletList().run(), editor.isActive('bulletList'))}
      {btn(t('compose.editor.quote'), <Quote />, () => c().toggleBlockquote().run(), editor.isActive('blockquote'))}
      <span className="sep" />
      <button
        type="button"
        className={`icon-btn${editor.isActive('link') ? ' active' : ''}`}
        aria-label={t('compose.editor.insertLink')}
        data-tip={t('compose.editor.insertLinkCtrlK')}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          setHref((editor.getAttributes('link').href as string | undefined) ?? '')
          linkMenu.toggle(e.currentTarget)
        }}
      >
        <Link2 />
      </button>
      {btn(t('compose.editor.removeFormatting'), <Eraser />, () => c().unsetAllMarks().clearNodes().run())}

      <Popover anchor={colorMenu.anchor} open={colorMenu.open} onClose={colorMenu.close} role="dialog" label={t('compose.editor.textColour')} minWidth={0}>
        <div className="color-grid" style={{ gridTemplateColumns: 'repeat(6, 24px)' }}>
          {TEXT_COLORS.map((col) => (
            <button
              key={col}
              type="button"
              className="color-swatch"
              style={{ background: col }}
              aria-label={t('compose.editor.colour', { col })}
              onClick={() => {
                c().setColor(col).run()
                colorMenu.close()
              }}
            />
          ))}
        </div>
        <button type="button" className="menu-item" onClick={() => { c().unsetColor().run(); colorMenu.close() }}>
          {t('common.defaultColour')}
        </button>
      </Popover>
      <Popover anchor={linkMenu.anchor} open={linkMenu.open} onClose={linkMenu.close} role="dialog" label={t('compose.editor.editLink')} minWidth={320}>
        <form
          style={{ padding: '8px 16px', display: 'flex', gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault()
            const url = href.trim()
            if (!url) c().extendMarkRange('link').unsetLink().run()
            else {
              const full = /^(https?:|mailto:)/i.test(url) ? url : `https://${url}`
              if (editor.state.selection.empty) c().insertContent(`<a href="${full.replace(/"/g, '&quot;')}">${url.replace(/</g, '&lt;')}</a>`).run()
              else c().extendMarkRange('link').setLink({ href: full }).run()
            }
            linkMenu.close()
          }}
        >
          <input className="input" autoFocus value={href} onChange={(e) => setHref(e.target.value)} placeholder="https://" aria-label={t('compose.editor.linkAddress')} />
          <button type="submit" className="btn filled sm">
            {t('compose.editor.ok')}
          </button>
        </form>
      </Popover>
    </div>
  )
}

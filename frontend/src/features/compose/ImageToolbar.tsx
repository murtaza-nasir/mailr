// ACT mail.insert_image @web.editor: the small toolbar under a selected image in the compose editor.
// Size presets, alignment, alt text, replace, remove and "Make it an attachment". The buttons
// form one toolbar: you reach it with Tab from the editor, move along it with the arrow keys
// and go back to the text with Escape. In the editor itself, you resize a selected image with
// - and +.
import type { Editor as TiptapEditor } from '@tiptap/react'
import { NodeSelection } from '@tiptap/pm/state'
import { AlignCenter, AlignLeft, AlignRight, ImageUp, Paperclip, Trash2 } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { activePreset, presetSize, type ImageAlign, type SizePreset } from './inlineImages'
import './inlineImages.css'
import { t } from '../../i18n'
import { arrowStep } from '../../lib/rtl'

export interface ImageToolbarProps {
  editor: TiptapEditor | null
  fullSize: boolean
  onFullSize: (on: boolean) => void
  onReplace: (pos: number) => void
  onMakeAttachment: (pos: number) => void
}

interface Selected {
  pos: number
  attrs: Record<string, unknown>
  img: HTMLImageElement | null
  top: number
  left: number
}

function selectedImage(editor: TiptapEditor): Selected | null {
  const sel = editor.state.selection
  if (!(sel instanceof NodeSelection) || sel.node.type.name !== 'image') return null
  const dom = editor.view.nodeDOM(sel.from) as HTMLElement | null
  const wrap = dom?.closest('.editor-wrap') as HTMLElement | null
  if (!dom || !wrap) return null
  const r = dom.getBoundingClientRect()
  const w = wrap.getBoundingClientRect()
  const left = Math.max(0, r.left - w.left + wrap.scrollLeft)
  return { pos: sel.from, attrs: sel.node.attrs, img: dom.querySelector('img'), top: r.bottom - w.top + wrap.scrollTop + 6, left }
}

export function ImageToolbar({ editor, fullSize, onFullSize, onReplace, onMakeAttachment }: ImageToolbarProps) {
  const [, force] = useState(0)
  const [altOpen, setAltOpen] = useState(false)
  const [alt, setAlt] = useState('')
  const barRef = useRef<HTMLDivElement>(null)
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
  const sel = editor ? selectedImage(editor) : null
  const selPos = sel?.pos
  useEffect(() => setAltOpen(false), [selPos])
  // Keep the whole toolbar inside the editor's width.
  useLayoutEffect(() => {
    const bar = barRef.current
    const wrap = bar?.parentElement
    if (!bar || !wrap || !sel) return
    bar.style.left = `${Math.max(0, Math.min(sel.left, wrap.clientWidth - bar.offsetWidth - 4))}px`
  })
  if (!editor || !sel) return null

  const natural = { width: sel.img?.naturalWidth || Number(sel.attrs.width) || 0, height: sel.img?.naturalHeight || Number(sel.attrs.height) || 0 }
  const preset = natural.width ? activePreset(Number(sel.attrs.width) || natural.width, natural) : null
  const align = (sel.attrs.align as ImageAlign) ?? null
  const uploading = !!sel.attrs.uploadKey
  // Each command keeps the image selected, so the toolbar stays where it is.
  const run = (fn: (c: ReturnType<TiptapEditor['chain']>) => ReturnType<TiptapEditor['chain']>) => fn(editor.chain().setNodeSelection(sel.pos)).setNodeSelection(sel.pos).run()
  const setSize = (p: SizePreset) => natural.width && run((c) => c.setImageSize(presetSize(p, natural)))
  const setAlign = (a: ImageAlign) => run((c) => c.setImageAlign(align === a ? null : a))

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (altOpen) return
    const items = Array.from(barRef.current?.querySelectorAll<HTMLElement>('button, input[type=checkbox]') ?? [])
    const i = items.indexOf(document.activeElement as HTMLElement)
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      items[(i + arrowStep(e.key) + items.length) % items.length]?.focus()
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      items[e.key === 'Home' ? 0 : items.length - 1]?.focus()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      editor.chain().setNodeSelection(sel.pos).focus().run()
    }
  }
  const btn = (label: string, body: ReactNode, onClick: () => void, opts: { pressed?: boolean; text?: boolean; disabled?: boolean } = {}) => (
    <button
      type="button"
      className={opts.text ? `img-tb-text${opts.pressed ? ' active' : ''}` : `icon-btn xs${opts.pressed ? ' active' : ''}`}
      aria-label={label}
      data-tip={opts.text ? undefined : label}
      aria-pressed={opts.pressed}
      disabled={opts.disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
    >
      {body}
    </button>
  )

  return (
    <div
      ref={barRef}
      className="img-toolbar"
      role="toolbar"
      aria-label={t('compose.imageToolbar.imageOptions')}
      aria-describedby="img-toolbar-keys"
      // rtl-ok: the image's own position, measured on the screen
      style={{ top: sel.top, left: sel.left }}
      onKeyDown={onKey}
      onClick={(e) => e.stopPropagation()}
    >
      <span id="img-toolbar-keys" hidden>{t('compose.imageToolbar.arrowKeysMoveBetweenOptions')}</span>
      {altOpen ? (
        <form
          className="img-alt-form"
          onSubmit={(e) => {
            e.preventDefault()
            run((c) => c.updateAttributes('image', { alt: alt.trim() || null }))
            setAltOpen(false)
            editor.commands.focus()
          }}
        >
          <input
            className="input"
            autoFocus
            value={alt}
            onChange={(e) => setAlt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                setAltOpen(false)
                editor.chain().setNodeSelection(sel.pos).focus().run()
              }
            }}
            placeholder={t('compose.imageToolbar.describeTheImage')}
            aria-label={t('compose.imageToolbar.altText')}
          />
          <button type="submit" className="btn filled sm">{t('common.save')}</button>
        </form>
      ) : (
        <>
          {btn(t('compose.imageToolbar.small'), t('compose.imageToolbar.small'), () => setSize('small'), { text: true, pressed: preset === 'small', disabled: !natural.width })}
          {btn(t('common.medium'), t('common.medium'), () => setSize('medium'), { text: true, pressed: preset === 'medium', disabled: !natural.width })}
          {btn(t('compose.imageToolbar.originalSize'), t('compose.imageToolbar.original'), () => setSize('original'), { text: true, pressed: preset === 'original', disabled: !natural.width })}
          <span className="sep" />
          {btn(t('compose.imageToolbar.alignLeft'), <AlignLeft />, () => setAlign('left'), { pressed: align === 'left' })}
          {btn(t('compose.imageToolbar.alignCentre'), <AlignCenter />, () => setAlign('center'), { pressed: align === 'center' })}
          {btn(t('compose.imageToolbar.alignRight'), <AlignRight />, () => setAlign('right'), { pressed: align === 'right' })}
          <span className="sep" />
          {btn(t('compose.imageToolbar.altText'), t('common.alt'), () => {
            setAlt(String(sel.attrs.alt ?? ''))
            setAltOpen(true)
          }, { text: true })}
          {btn(t('compose.imageToolbar.replaceImage'), <ImageUp />, () => onReplace(sel.pos), { disabled: uploading })}
          {btn(t('compose.imageToolbar.makeItAnAttachment'), <Paperclip />, () => onMakeAttachment(sel.pos), { disabled: uploading })}
          {btn(t('compose.imageToolbar.removeImage'), <Trash2 />, () => editor.chain().setNodeSelection(sel.pos).deleteSelection().focus().run())}
          <span className="sep" />
          <label className="img-tb-check" data-tip={t('compose.imageToolbar.imagesLargerThan2560Pixels')}>
            <input type="checkbox" checked={fullSize} onChange={(e) => onFullSize(e.target.checked)} />
            {t('compose.imageToolbar.sendImagesAtFullSize')}
          </label>
        </>
      )}
    </div>
  )
}

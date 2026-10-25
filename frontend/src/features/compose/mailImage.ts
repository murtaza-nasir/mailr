// The compose editor's image node (ACT mail.insert_image @web.editor): TipTap's Image, extended with the
// size and alignment that go out in the mail, corner handles that keep the aspect ratio, and a
// progress bar during the upload.
import Image from '@tiptap/extension-image'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { Editor } from '@tiptap/core'
import { alignFromElement, dragWidth, heightFor, imageStyle, type ImageAlign } from './inlineImages'

// ----- upload progress, by the placeholder's key -----
const progress = new Map<string, number>()
const listeners = new Map<string, Set<(f: number) => void>>()

export function setUploadProgress(key: string, f: number | null) {
  if (f === null) progress.delete(key)
  else progress.set(key, f)
  for (const fn of listeners.get(key) ?? []) fn(f ?? 1)
}

function onProgress(key: string, fn: (f: number) => void): () => void {
  const set = listeners.get(key) ?? new Set()
  set.add(fn)
  listeners.set(key, set)
  return () => set.delete(fn)
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    mailImage: {
      /** Size of the selected image, in CSS pixels (height follows the aspect ratio). */
      setImageSize: (size: { width: number; height: number }) => ReturnType
      /** Alignment of the selected image; null puts it back in the line of text. */
      setImageAlign: (align: ImageAlign) => ReturnType
    }
  }
}

const CORNERS = ['nw', 'ne', 'sw', 'se'] as const

/** Image sources the editor may load: data: and blob: images, cid: parts and mailr's own paths
 *  (uploads, the image proxy). A remote address never loads in the editor: it stays in
 *  data-mailr-src (shown as a placeholder) and the server puts it back when the mail is sent. */
export function editorSafeSrc(src: string | null | undefined): boolean {
  const s = (src ?? '').trim()
  if (!s) return false
  if (/^(data:image\/|blob:|cid:)/i.test(s)) return true
  if (s.startsWith('/') && !s.startsWith('//') && !s.startsWith('/\\')) return true
  try {
    return typeof location !== 'undefined' && new URL(s).origin === location.origin
  } catch {
    return false
  }
}

const REMOTE = /^(?:https?:)?\/\/[^/\s]/i

/** The node's src and remote address from an <img> element (see editorSafeSrc). */
export function imageSources(el: Element): { src: string | null; mailrSrc: string | null } {
  const src = el.getAttribute('src')
  const kept = el.getAttribute('data-mailr-src')
  const mailrSrc = kept && REMOTE.test(kept) ? kept : src && REMOTE.test(src) && !editorSafeSrc(src) ? src : null
  return { src: editorSafeSrc(src) ? src : null, mailrSrc }
}

export const MailImage = Image.extend({
  // A quoted remote image held back has no src at all, only data-mailr-src.
  parseHTML() {
    return [{ tag: this.options.allowBase64 ? 'img[src]' : 'img[src]:not([src^="data:"])' }, { tag: 'img[data-mailr-src]' }]
  },

  addAttributes() {
    return {
      ...this.parent?.(),
      src: {
        default: null,
        parseHTML: (el: HTMLElement) => imageSources(el).src,
        renderHTML: (a: Record<string, unknown>) => ({ src: editorSafeSrc(a.src as string) ? a.src : '' }),
      },
      // A quoted image's own address (the reader's images were blocked, or it shows through the
      // proxy). Written into the HTML so the server can send the original.
      mailrSrc: {
        default: null,
        parseHTML: (el: HTMLElement) => imageSources(el).mailrSrc,
        renderHTML: (a: Record<string, unknown>) => (a.mailrSrc ? { 'data-mailr-src': a.mailrSrc } : {}),
      },
      width: {
        default: null,
        parseHTML: (el: HTMLElement) => Number(el.getAttribute('width')) || null,
        renderHTML: (a: Record<string, unknown>) => (a.width ? { width: a.width } : {}),
      },
      height: {
        default: null,
        parseHTML: (el: HTMLElement) => Number(el.getAttribute('height')) || null,
        renderHTML: (a: Record<string, unknown>) => (a.height ? { height: a.height } : {}),
      },
      align: {
        default: null,
        parseHTML: (el: HTMLElement) => alignFromElement(el),
        renderHTML: (a: Record<string, unknown>) => ({
          ...(a.align ? { 'data-align': a.align } : {}),
          style: imageStyle((a.align as ImageAlign) ?? null),
        }),
      },
      // A placeholder that is still uploading. Never written into the HTML.
      uploadKey: { default: null, parseHTML: () => null, renderHTML: () => ({}) },
    }
  },

  addCommands() {
    return {
      ...this.parent?.(),
      setImageSize:
        (size) =>
        ({ commands }) =>
          commands.updateAttributes(this.name, { width: Math.round(size.width), height: Math.round(size.height) }),
      setImageAlign:
        (align) =>
        ({ commands }) =>
          commands.updateAttributes(this.name, { align }),
    }
  },

  addNodeView() {
    return ({ node, editor, getPos }) => new ImageView(node, editor as Editor, getPos as () => number | undefined)
  },
})

class ImageView {
  dom: HTMLElement
  img: HTMLImageElement
  bar: HTMLElement
  node: PMNode
  unlisten: (() => void) | null = null
  editor: Editor
  getPos: () => number | undefined

  constructor(node: PMNode, editor: Editor, getPos: () => number | undefined) {
    this.node = node
    this.editor = editor
    this.getPos = getPos
    this.dom = document.createElement('span')
    this.dom.className = 'mail-img'
    this.dom.contentEditable = 'false'
    this.img = document.createElement('img')
    this.img.draggable = false
    this.dom.appendChild(this.img)
    for (const c of CORNERS) {
      const h = document.createElement('span')
      h.className = `mail-img-handle ${c}`
      h.dataset.corner = c
      h.setAttribute('aria-hidden', 'true')
      h.addEventListener('pointerdown', (e) => this.startDrag(e, c))
      this.dom.appendChild(h)
    }
    this.bar = document.createElement('span')
    this.bar.className = 'mail-img-progress'
    this.bar.setAttribute('role', 'progressbar')
    this.bar.setAttribute('aria-label', 'Uploading image')
    this.bar.appendChild(document.createElement('span'))
    this.dom.appendChild(this.bar)
    this.render()
  }

  render() {
    const a = this.node.attrs
    const src = editorSafeSrc(a.src) ? (a.src as string) : ''
    if (src) {
      if (this.img.getAttribute('src') !== src) this.img.src = src
    } else {
      this.img.removeAttribute('src')
    }
    // A remote image held back: a box with its description instead of the picture.
    this.dom.classList.toggle('blocked', !src && !!a.mailrSrc)
    if (!src && a.mailrSrc && !a.width) this.img.style.minWidth = '48px'
    this.img.alt = a.alt ?? ''
    if (a.title) this.img.title = a.title
    if (a.width) this.img.style.width = `${a.width}px`
    else this.img.style.removeProperty('width')
    this.dom.dataset.align = a.align ?? ''
    this.unlisten?.()
    this.unlisten = null
    if (a.uploadKey) {
      this.dom.classList.add('uploading')
      const set = (f: number) => {
        ;(this.bar.firstChild as HTMLElement).style.width = `${Math.round(f * 100)}%`
        this.bar.setAttribute('aria-valuenow', String(Math.round(f * 100)))
      }
      set(progress.get(a.uploadKey) ?? 0)
      this.unlisten = onProgress(a.uploadKey, set)
    } else {
      this.dom.classList.remove('uploading')
    }
  }

  startDrag(e: PointerEvent, corner: (typeof CORNERS)[number]) {
    if (!this.editor.isEditable) return
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const start = this.img.getBoundingClientRect().width
    const natural = { width: this.img.naturalWidth || start, height: this.img.naturalHeight || this.img.getBoundingClientRect().height }
    const max = (this.editor.view.dom as HTMLElement).clientWidth || Infinity
    let width = start
    const target = e.target as HTMLElement
    target.setPointerCapture?.(e.pointerId)
    this.dom.classList.add('resizing')
    const move = (ev: PointerEvent) => {
      width = dragWidth(start, ev.clientX - startX, corner, max)
      this.img.style.width = `${width}px`
    }
    const up = () => {
      target.removeEventListener('pointermove', move)
      target.removeEventListener('pointerup', up)
      target.removeEventListener('pointercancel', up)
      this.dom.classList.remove('resizing')
      const pos = this.getPos()
      if (pos === undefined) return
      this.editor.chain().setNodeSelection(pos).setImageSize({ width, height: heightFor(width, natural) }).focus().run()
    }
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', up)
    target.addEventListener('pointercancel', up)
  }

  update(node: PMNode) {
    if (node.type !== this.node.type) return false
    this.node = node
    this.render()
    return true
  }

  selectNode() {
    this.dom.classList.add('selected')
  }

  deselectNode() {
    this.dom.classList.remove('selected')
  }

  stopEvent(e: Event) {
    return !!(e.target as HTMLElement | null)?.dataset?.corner
  }

  ignoreMutation() {
    return true
  }

  destroy() {
    this.unlisten?.()
  }
}

// Inline images in compose (ACT mail.insert_image @web.editor): limits, the downscale decision, size
// presets and alignment. Kept free of the editor so the rules can be tested on their own.

import { t } from '../../i18n'

export const INLINE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const
export const INLINE_ACCEPT = INLINE_TYPES.join(',')
/** The server's per-image limit for inline parts (core/compose.py MAX_INLINE). */
export const MAX_INLINE_BYTES = 10 * 1024 * 1024
/** Images over either limit are reduced before upload unless "Send images at full size" is on. */
export const DOWNSCALE_SIDE = 2560
export const DOWNSCALE_BYTES = 3 * 1024 * 1024
/** Width of a new image in the message: its own width, at most this. */
export const INSERT_MAX_WIDTH = 600
export const MIN_WIDTH = 24

export type SizePreset = 'small' | 'medium' | 'original'
export const SIZE_PRESETS: Record<Exclude<SizePreset, 'original'>, number> = { small: 200, medium: 400 }

export type ImageAlign = 'left' | 'center' | 'right' | null

const UPLOAD_SRC = /^(?:https?:\/\/[^/]+)?\/api\/v1\/uploads\/([0-9a-f]{32})$/

export function uploadSrc(blob: string): string {
  return `/api/v1/uploads/${blob}`
}

/** The upload id an image's src points at, or null (a remote or data: image). */
export function blobFromSrc(src: string | null | undefined): string | null {
  const m = UPLOAD_SRC.exec(src ?? '')
  return m ? m[1] : null
}

/** Whether a file can go into the message body; the reason when it cannot. */
export function checkInlineFile(file: { name: string; type: string; size: number }): { ok: true } | { ok: false; reason: string } {
  const type = (file.type || '').toLowerCase()
  if (type === 'image/svg+xml' || /\.svgz?$/i.test(file.name)) {
    return { ok: false, reason: t('compose.inlineImages.isAnSvgImageSvg', { fileName: file.name }) }
  }
  if (!(INLINE_TYPES as readonly string[]).includes(type)) {
    return { ok: false, reason: t('compose.inlineImages.isNotAPngJpeg', { fileName: file.name }) }
  }
  return { ok: true }
}

/** Whether an image is reduced before upload. GIFs are never redrawn (only the first frame of
 *  an animation is drawn on a canvas). */
export function shouldDownscale(img: { width: number; height: number; size: number; type: string }, fullSize: boolean): boolean {
  if (fullSize || img.type === 'image/gif') return false
  return Math.max(img.width, img.height) > DOWNSCALE_SIDE || img.size > DOWNSCALE_BYTES
}

/** The pixel size of a reduced image: the longer side at most `max`, aspect ratio kept. */
export function downscaleSize(width: number, height: number, max = DOWNSCALE_SIDE): { width: number; height: number } {
  const long = Math.max(width, height)
  if (long <= max) return { width, height }
  const f = max / long
  return { width: Math.max(1, Math.round(width * f)), height: Math.max(1, Math.round(height * f)) }
}

/** Height for a width, from the image's own proportions. */
export function heightFor(width: number, natural: { width: number; height: number }): number {
  if (!natural.width) return width
  return Math.max(1, Math.round((width * natural.height) / natural.width))
}

/** The size a new image gets in the message. */
export function initialSize(natural: { width: number; height: number }): { width: number; height: number } {
  const width = Math.min(natural.width, INSERT_MAX_WIDTH)
  return { width, height: heightFor(width, natural) }
}

/** Small, Medium and Original. A preset never makes an image larger than it is. */
export function presetSize(preset: SizePreset, natural: { width: number; height: number }): { width: number; height: number } {
  const width = preset === 'original' ? natural.width : Math.min(SIZE_PRESETS[preset], natural.width)
  return { width, height: heightFor(width, natural) }
}

/** The preset a width matches, for the toolbar's pressed state. */
export function activePreset(width: number | null | undefined, natural: { width: number; height: number }): SizePreset | null {
  if (!width) return null
  for (const p of ['small', 'medium', 'original'] as const) if (presetSize(p, natural).width === width) return p
  return null
}

/** Resize by a step (keyboard: - and +), aspect ratio kept, within MIN_WIDTH and `max`. */
export function stepSize(width: number, natural: { width: number; height: number }, dir: 1 | -1, max = Infinity): { width: number; height: number } {
  const step = Math.max(10, Math.round(width * 0.1))
  const w = Math.min(Math.max(MIN_WIDTH, width + dir * step), Math.max(MIN_WIDTH, max))
  return { width: w, height: heightFor(w, natural) }
}

/** The width a corner drag gives: right-hand corners grow to the right, left-hand ones to the left. */
export function dragWidth(start: number, dx: number, corner: 'nw' | 'ne' | 'sw' | 'se', max = Infinity): number {
  const w = corner === 'ne' || corner === 'se' ? start + dx : start - dx
  return Math.round(Math.min(Math.max(MIN_WIDTH, w), max))
}

/** Inline CSS for an image in sent mail. Width and height also go out as attributes for
 *  clients that ignore CSS. */
export function imageStyle(align: ImageAlign): string {
  const base = 'max-width:100%;height:auto'
  if (align === 'center') return `${base};display:block;margin-left:auto;margin-right:auto`
  if (align === 'right') return `${base};display:block;margin-left:auto;margin-right:0`
  if (align === 'left') return `${base};display:block;margin-left:0;margin-right:auto`
  return base
}

/** Alignment read back from saved HTML (data-align, else the margins imageStyle writes). */
export function alignFromElement(el: { getAttribute(n: string): string | null }): ImageAlign {
  const a = el.getAttribute('data-align')
  if (a === 'left' || a === 'center' || a === 'right') return a
  const style = (el.getAttribute('style') ?? '').replace(/\s+/g, '')
  if (!/display:block/.test(style)) return null
  const left = /margin-left:auto/.test(style)
  const right = /margin-right:auto/.test(style)
  return left && right ? 'center' : left ? 'right' : right ? 'left' : null
}

/** File name for an image that becomes an attachment. */
export function attachmentName(type: string, alt?: string | null): string {
  const ext = type === 'image/jpeg' ? 'jpg' : type.split('/')[1] || 'png'
  const base = (alt ?? '').trim().replace(/[\\/:*?"<>|]+/g, '').slice(0, 60) || 'image'
  return `${base}.${ext}`
}

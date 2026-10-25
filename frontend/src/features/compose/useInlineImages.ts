// ACT mail.insert_image @web.editor: images in the compose body. Each image is uploaded through /uploads
// (the same store as attachments) and shown from there; until the upload ends, a faded
// placeholder with a progress bar is shown in its place. Large images are reduced first unless
// the message is set to "Send images at full size".
import type { Editor as TiptapEditor } from '@tiptap/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadFile, type UploadResult } from '../../api/client'
import { toast } from '../../stores/ui'
import {
  attachmentName, blobFromSrc, checkInlineFile, downscaleSize, heightFor, initialSize, MAX_INLINE_BYTES, shouldDownscale, uploadSrc,
} from './inlineImages'
import { setUploadProgress } from './mailImage'
import { t } from '../../i18n'

export interface InlineAttachment {
  blob: string
  filename: string
  type: string
  size: number
}

async function imageDims(file: Blob): Promise<{ width: number; height: number }> {
  const bmp = await createImageBitmap(file)
  const d = { width: bmp.width, height: bmp.height }
  bmp.close()
  return d
}

/** The image redrawn at most DOWNSCALE_SIDE on its longer side; the original when the result
 *  would not be smaller. */
async function downscale(file: File, dims: { width: number; height: number }): Promise<{ file: File; width: number; height: number }> {
  const to = downscaleSize(dims.width, dims.height)
  const bmp = await createImageBitmap(file)
  const canvas = document.createElement('canvas')
  canvas.width = to.width
  canvas.height = to.height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error(t('compose.useInlineImages.noCanvas'))
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(bmp, 0, 0, to.width, to.height)
  bmp.close()
  const type = file.type === 'image/png' || file.type === 'image/webp' ? file.type : 'image/jpeg'
  const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, type, 0.88))
  if (!blob || blob.size >= file.size) return { file, ...dims }
  return { file: new File([blob], file.name, { type }), ...to }
}

let seq = 1

export function useInlineImages(editor: TiptapEditor | null, opts: { onAttach: (a: InlineAttachment) => void }) {
  const [busy, setBusy] = useState(0)
  const [fullSize, setFullSizeState] = useState(false)
  const fullSizeRef = useRef(false)
  // Originals of the images that were reduced, by the upload of the reduced copy.
  const originals = useRef(new Map<string, File>())
  const onAttachRef = useRef(opts.onAttach)
  useEffect(() => {
    onAttachRef.current = opts.onAttach
  })

  const findKey = useCallback((key: string): number | null => {
    let found: number | null = null
    editor?.state.doc.descendants((n, pos) => {
      if (found !== null) return false
      if (n.type.name === 'image' && n.attrs.uploadKey === key) found = pos
      return true
    })
    return found
  }, [editor])

  const patch = useCallback((pos: number, attrs: Record<string, unknown>) => {
    if (!editor) return
    const node = editor.state.doc.nodeAt(pos)
    if (!node) return
    editor.view.dispatch(editor.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, ...attrs }).setMeta('addToHistory', false))
  }, [editor])

  /** Upload `file` for the placeholder `key`; on success the node points at the upload. */
  const upload = useCallback((file: File, key: string, reducedFrom?: File): Promise<UploadResult | null> => {
    setBusy((n) => n + 1)
    setUploadProgress(key, 0)
    const up = uploadFile(file, (f) => setUploadProgress(key, f))
    return up.promise
      .then((r) => {
        const pos = findKey(key)
        if (pos !== null) patch(pos, { src: uploadSrc(r.blob), uploadKey: null })
        if (reducedFrom) originals.current.set(r.blob, reducedFrom)
        return r
      })
      .catch((e: Error) => {
        const pos = findKey(key)
        if (pos !== null && editor) editor.view.dispatch(editor.state.tr.delete(pos, pos + 1).setMeta('addToHistory', false))
        toast({ message: t('compose.useInlineImages.wasNotInserted', { fileName: file.name, message: e.message }), error: true })
        return null
      })
      .finally(() => {
        setUploadProgress(key, null)
        setBusy((n) => n - 1)
      })
  }, [editor, findKey, patch])

  /** Checks, reduces when needed, and returns the file to upload with its pixel size. */
  const prepare = useCallback(async (file: File) => {
    const check = checkInlineFile(file)
    if (!check.ok) {
      toast({ message: check.reason, error: true })
      return null
    }
    let dims: { width: number; height: number }
    try {
      dims = await imageDims(file)
    } catch {
      toast({ message: t('compose.useInlineImages.couldNotBeReadAs', { fileName: file.name }), error: true })
      return null
    }
    let out = { file, ...dims }
    if (shouldDownscale({ ...dims, size: file.size, type: file.type }, fullSizeRef.current)) {
      try {
        out = await downscale(file, dims)
      } catch {
        /* sent as it is */
      }
    }
    if (out.file.size > MAX_INLINE_BYTES) {
      toast({ message: t('compose.useInlineImages.isLargerThan10Mb', { fileName: file.name }), error: true })
      return null
    }
    return { ...out, reduced: out.file !== file }
  }, [])

  /** "Send images at full size": the reduced images of this message go up again as they were. */
  const setFullSize = useCallback((on: boolean) => {
    fullSizeRef.current = on
    setFullSizeState(on)
    if (!on || !editor) return
    const redo: { pos: number; blob: string }[] = []
    editor.state.doc.descendants((n, pos) => {
      const blob = n.type.name === 'image' ? blobFromSrc(n.attrs.src) : null
      if (blob && originals.current.has(blob)) redo.push({ pos, blob })
    })
    for (const { pos, blob } of redo) {
      const file = originals.current.get(blob)!
      originals.current.delete(blob)
      const key = `img${seq++}`
      patch(pos, { uploadKey: key })
      void upload(file, key)
    }
  }, [editor, patch, upload])

  const insert = useCallback(async (files: File[], at?: number) => {
    if (!editor) return
    for (const file of files) {
      const p = await prepare(file)
      if (!p) continue
      const key = `img${seq++}`
      const url = URL.createObjectURL(p.file)
      const size = initialSize(p)
      const node = { type: 'image', attrs: { src: url, alt: null, ...size, uploadKey: key } }
      const chain = editor.chain().focus()
      ;(at !== undefined ? chain.insertContentAt(at, node) : chain.insertContent(node)).run()
      void upload(p.file, key, p.reduced ? file : undefined).then((r) => {
        setTimeout(() => URL.revokeObjectURL(url), 10_000)
        if (r && p.reduced) {
          toast({
            message: t('compose.useInlineImages.imageReducedToForSending', { width: p.width, height: p.height }),
            actionLabel: t('compose.useInlineImages.sendAtFullSize'),
            onAction: () => setFullSize(true),
          })
        }
      })
    }
  }, [editor, prepare, upload, setFullSize])

  /** Swap the image at `pos` for `file`, keeping its width, alignment and alt text. */
  const replace = useCallback(async (pos: number, file: File) => {
    if (!editor) return
    const node = editor.state.doc.nodeAt(pos)
    if (!node || node.type.name !== 'image') return
    const p = await prepare(file)
    if (!p) return
    const key = `img${seq++}`
    const width = Math.min(Number(node.attrs.width) || initialSize(p).width, p.width)
    patch(pos, { src: URL.createObjectURL(p.file), width, height: heightFor(width, p), uploadKey: key })
    await upload(p.file, key, p.reduced ? file : undefined)
  }, [editor, prepare, patch, upload])

  /** "Make it an attachment": the image leaves the body and joins the attachments. */
  const toAttachment = useCallback(async (pos: number) => {
    if (!editor) return
    const node = editor.state.doc.nodeAt(pos)
    if (!node || node.type.name !== 'image') return
    const src = String(node.attrs.src ?? '')
    let blob = blobFromSrc(src)
    let data: Blob
    try {
      data = await (await fetch(src, { credentials: 'same-origin' })).blob()
      if (!blob) {
        const r = await uploadFile(new File([data], attachmentName(data.type, node.attrs.alt), { type: data.type })).promise
        blob = r.blob
      }
    } catch {
      toast({ message: t('compose.useInlineImages.thisImageCannotBeMade'), error: true })
      return
    }
    const type = data.type || 'image/png'
    onAttachRef.current({ blob, filename: attachmentName(type, node.attrs.alt), type, size: data.size })
    const at = editor.state.doc.nodeAt(pos) === node ? pos : null
    if (at !== null) editor.chain().setNodeSelection(at).deleteSelection().focus().run()
  }, [editor])

  return { insert, replace, setFullSize, fullSize, toAttachment, busy: busy > 0 }
}

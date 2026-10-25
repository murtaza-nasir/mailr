import { useCallback, useState } from 'react'
import { uploadFile, type UploadResult } from '../../api/client'
import type { ComposeAttachment } from '../../api/types'
import { toast } from '../../stores/ui'
import { attKey, heldAttachment, type PendingAttachment } from './composeModel'
import { t } from '../../i18n'

const MAX_ATTACHMENT = 25 * 1024 * 1024

/** The compose form's attachment list: the draft's files, and uploads with their progress.
 *  `onUploaded` runs when an upload finishes (the draft then needs saving). */
export function useAttachments(initial: ComposeAttachment[] | undefined, onUploaded: () => void) {
  const [atts, setAtts] = useState<PendingAttachment[]>(() => (initial ?? []).map(heldAttachment))
  const addFiles = useCallback((files: File[]) => {
    for (const file of files) {
      if (file.size > MAX_ATTACHMENT) {
        toast({ message: t('compose.useAttachments.isLargerThan25Mb', { fileName: file.name }), error: true })
        continue
      }
      const key = attKey()
      const up = uploadFile(file, (f) => setAtts((list) => list.map((a) => (a.key === key ? { ...a, progress: f } : a))))
      setAtts((list) => [...list, { key, filename: file.name, type: file.type || 'application/octet-stream', size: file.size, progress: 0, abort: up.abort }])
      up.promise
        .then((r: UploadResult) => {
          setAtts((list) => list.map((a) => (a.key === key ? { ...a, blob: r.blob, filename: r.filename || a.filename, type: r.type || a.type, size: r.size || a.size, progress: 1, abort: undefined } : a)))
          onUploaded()
        })
        .catch((e: Error) => {
          setAtts((list) => list.map((a) => (a.key === key ? { ...a, error: e.message, abort: undefined } : a)))
        })
    }
    // The form passes a function that only sets state, so the first one serves for good.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const uploading = atts.some((a) => !a.blob && !a.error)
  return { atts, setAtts, addFiles, uploading }
}

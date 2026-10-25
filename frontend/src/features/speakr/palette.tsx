// The command palette's recording entries (web.palette), shown only with a
// Speakr account: "Go to Recordings" (the app, g r), "New recording" (the browser recorder),
// "Open recording…" (type a title; the matches open in the sheet), "Search
// recordings for …" (mail search with recording:<text>, whose Recordings section lists them),
// "Follow-up from the latest meeting", and, while a recording is open in the sheet, its Follow-up
// email, Add tasks and Open in Speakr.

import { useQueryClient } from '@tanstack/react-query'
import { AudioLines, CheckSquare, ExternalLink, Mail, Mic, Play, Search } from 'lucide-react'
import { useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { appHome } from '../../lib/routes'
import { mailPath } from '../../lib/views'
import { useRecorder } from '../recordings/recorderStore'
import { toast } from '../../stores/ui'
import { startFollowUp } from './actions'
import { speakrKeys, useRecordingList, useSpeakrStatus, type Recording } from './api'
import { useRecordingSheet } from './store'
import { t, appLocale } from '../../i18n'

export interface RecordingPaletteItem {
  id: string
  section: 'Recordings'
  label: string
  hint?: string
  icon: ReactNode
  keys?: string
  chips?: string[]
  stay?: boolean
  /** Guidance, not a command: no ↵ (CommandPalette). */
  info?: boolean
  run: () => void
}

const PREFIX = /^recording:\s*/i

/** "Recording: …" in the palette lists recordings by title. */
export function recordingMode(q: string): string | null {
  return PREFIX.test(q) ? q.replace(PREFIX, '') : null
}

// ACT recording.view @web.palette, ACT recording.search @web.palette, ACT recording.follow_up @web.palette, ACT recording.record @web.palette
export function useRecordingPaletteItems(q: string, setQ: (s: string) => void): {
  fixed: RecordingPaletteItem[]; dynamic: RecordingPaletteItem[]; only: boolean; hint: RecordingPaletteItem
} {
  const nav = useNavigate()
  const qc = useQueryClient()
  const status = useSpeakrStatus()
  const on = !!status.data?.connected
  const open = useRecordingSheet((s) => s.open)
  const mode = recordingMode(q)
  const list = useRecordingList(mode?.trim() ?? '', on && mode !== null)

  const fixed = useMemo<RecordingPaletteItem[]>(() => {
    if (!on) return []
    const out: RecordingPaletteItem[] = [
      { id: 'rec:app', section: 'Recordings', label: t('speakr.palette.goToRecordings'), icon: <AudioLines />, keys: 'g r', run: () => nav(appHome('recordings')) },
      { id: 'rec:new', section: 'Recordings', label: t('common.newRecording'), hint: t('speakr.palette.recordInTheBrowser'), icon: <Mic />, run: () => useRecorder.getState().open() },
      { id: 'rec:open', section: 'Recordings', label: t('speakr.palette.openRecording'), hint: t('speakr.palette.typeATitle'), icon: <Play />, stay: true, run: () => setQ('Recording: ') },
      {
        id: 'rec:followup-latest', section: 'Recordings', label: t('speakr.palette.followUpFromTheLatest'), icon: <Mail />,
        run: () => {
          void api.get<{ items: Recording[] }>('/speakr/recordings', { status: 'completed', limit: 1 }).then((r) => {
            const last = r.items[0]
            if (!last) return toast({ message: t('speakr.palette.noFinishedRecordingYet') })
            void startFollowUp(last.connector_id, last.recording_id, 'summary')
          })
        },
      },
    ]
    if (open) {
      const r = qc.getQueryData<Recording>(speakrKeys.recording(open.cid, open.rid))
      const name = r?.title ? `“${r.title}”` : t('speakr.palette.thisRecording')
      out.push(
        { id: 'rec:this:followup', section: 'Recordings', label: t('speakr.palette.followUpEmailFor', { itemName: name }), icon: <Mail />, run: () => void startFollowUp(open.cid, open.rid, 'summary') },
        { id: 'rec:this:tasks', section: 'Recordings', label: t('speakr.palette.addTasksFrom', { itemName: name }), icon: <CheckSquare />, run: () => useRecordingSheet.getState().showTasks({ cid: open.cid, rid: open.rid, title: r?.title ?? t('common.recording'), ev: null }) },
      )
      if (r?.url) out.push({ id: 'rec:this:speakr', section: 'Recordings', label: t('speakr.palette.openInSpeakr', { itemName: name }), icon: <ExternalLink />, run: () => window.open(r.url, '_blank', 'noopener,noreferrer') })
    }
    return out
  }, [on, open, qc, setQ, nav])

  const dynamic = useMemo<RecordingPaletteItem[]>(() => {
    if (!on) return []
    if (mode !== null) {
      return (list.data?.items ?? []).slice(0, 8).map((r) => ({
        id: `rec:item:${r.connector_id}:${r.recording_id}`, section: 'Recordings' as const, label: t('speakr.palette.openRecording2', { title: r.title || t('common.untitled') }),
        hint: r.meeting_date ? new Date(r.meeting_date).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' }) : undefined,
        icon: <Play />, run: () => useRecordingSheet.getState().show(r.connector_id, r.recording_id),
      }))
    }
    const text = q.trim()
    if (!text) return []
    const quoted = /\s/.test(text) ? `"${text.replace(/"/g, '')}"` : text
    return [{ id: 'rec:search', section: 'Recordings', label: t('speakr.palette.searchRecordingsFor', { text }), icon: <Search />, run: () => nav(mailPath('search', null, `recording:${quoted}`)) }]
  }, [on, mode, list.data, q, nav])

  const hint: RecordingPaletteItem = { id: 'rec:hint', section: 'Recordings', label: list.isFetching ? t('speakr.palette.looking') : t('speakr.palette.typePartOfARecordings'), icon: <Play />, stay: true, info: true, run: () => {} }
  return { fixed, dynamic, only: mode !== null && on, hint }
}

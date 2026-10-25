// The state of a recording made in the browser (recording.record, web.recordings). It lives at the
// app level, so a recording goes on while the user moves between mail, calendar and tasks with
// the dialog minimised to a small bar (RecorderHost). The engine (recorder.ts) writes the phase,
// the time and the level here; the dialog (NewRecording.tsx) reads them and keeps its form here.

import { create } from 'zustand'

export type RecorderPhase = 'idle' | 'asking' | 'recording' | 'paused' | 'stopped' | 'uploading'

/** What the dialog sends with the audio (POST /speakr/recordings). */
export interface RecorderForm {
  title: string
  participants: string[]
  /** datetime-local value (local time), '' for the time the recording started. */
  meetingDate: string
  notes: string
  folder: string
  tags: string[]
  /** The calendar occurrence to link to, or null. */
  event: { uid: string; occurrence: string; summary: string; start: string } | null
}

export const EMPTY_FORM: RecorderForm = { title: '', participants: [], meetingDate: '', notes: '', folder: '', tags: [], event: null }

interface RecorderState {
  /** The dialog is open (else a recording in progress shows as the minimised bar). */
  dialog: boolean
  phase: RecorderPhase
  /** When the current recording started (epoch ms). */
  startedAt: number | null
  elapsedMs: number
  /** The microphone level, 0 to 1. */
  level: number
  error: string | null
  /** Upload progress, 0 to 1, while uploading. */
  progress: number | null
  /** The finished audio, once stopped (or recovered from IndexedDB). */
  blob: Blob | null
  sessionId: string | null
  /** The audio came from a recording a closed tab left behind. */
  recovered: boolean
  form: RecorderForm
  /** Recordings a closed tab left in IndexedDB, not uploaded yet (shown in the app). */
  pending: PendingRecording[]
  set: (p: Partial<RecorderState>) => void
  setForm: (p: Partial<RecorderForm>) => void
  /** Open the dialog; with a form, prefill it (only while nothing is being recorded). */
  open: (form?: Partial<RecorderForm>) => void
}

export interface PendingRecording {
  id: string
  startedAt: number
  durationMs: number
  bytes: number
  title: string
}

export const useRecorder = create<RecorderState>((set, get) => ({
  dialog: false,
  phase: 'idle',
  startedAt: null,
  elapsedMs: 0,
  level: 0,
  error: null,
  progress: null,
  blob: null,
  sessionId: null,
  recovered: false,
  form: EMPTY_FORM,
  pending: [],
  set: (p) => set(p),
  setForm: (p) => set({ form: { ...get().form, ...p } }),
  open: (form) => {
    const idle = get().phase === 'idle'
    set({ dialog: true, ...(idle && form ? { form: { ...EMPTY_FORM, ...form } } : {}) })
  },
}))

/** "3:07", "1:02:45". */
export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

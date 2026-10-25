// Recording in the browser (recording.record, web.recordings): the microphone is asked for on the
// user's click, MediaRecorder writes a chunk every two seconds, each chunk goes to IndexedDB
// (vault.ts) as it arrives, and an AnalyserNode feeds the level meter. Pause and resume keep one
// recording. Stop joins the chunks into one file, which the upload sends to mailr
// (POST /api/v1/speakr/recordings, multipart) with progress. A session stays in IndexedDB until its
// upload succeeds or the user discards it; a tab closed in the middle leaves it there, and the
// Recordings app offers it again (pendingRecordings).

// ACT recording.record @web.recordings
import { API, ApiError, getCsrf, notifyUnauthorized } from '../../api/client'
import { codedText } from '../../api/serverMessages'
import { EMPTY_FORM, useRecorder, type PendingRecording, type RecorderForm } from './recorderStore'
import * as vault from './vault'
import { t as tr } from '../../i18n'

const TIMESLICE_MS = 2000
const MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']

let mr: MediaRecorder | null = null
let stream: MediaStream | null = null
let audio: AudioContext | null = null
let meter: number | null = null
let chunks: Blob[] = []
let seq = 0
let bytes = 0
let mime = ''
let accMs = 0
let segStart = 0
let tick: number | null = null
let stopped: ((b: Blob) => void) | null = null

const st = () => useRecorder.getState()

export function supported(): boolean {
  return typeof window !== 'undefined' && typeof window.MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

function pickMime(): string {
  const can = (m: string) => {
    try {
      return typeof MediaRecorder.isTypeSupported !== 'function' || MediaRecorder.isTypeSupported(m)
    } catch {
      return false
    }
  }
  return MIMES.find(can) ?? ''
}

const elapsed = () => accMs + (st().phase === 'recording' ? Date.now() - segStart : 0)

function onBeforeUnload(e: BeforeUnloadEvent) {
  // The audio so far is in IndexedDB either way; the browser still asks, so the user can stay.
  e.preventDefault()
}

export function micError(e: unknown): string {
  const name = (e as { name?: string } | null)?.name ?? ''
  if (name === 'NotAllowedError' || name === 'SecurityError') return tr('recordings.recorder.microphoneAccessWasRefusedAllow')
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return tr('recordings.recorder.noMicrophoneWasFound')
  if (name === 'NotReadableError') return tr('recordings.recorder.theMicrophoneIsInUse')
  return e instanceof Error ? e.message : tr('recordings.recorder.theMicrophoneCouldNotBe')
}

function startMeter(s: MediaStream) {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctx) return
    audio = new Ctx()
    const analyser = audio.createAnalyser()
    analyser.fftSize = 1024
    audio.createMediaStreamSource(s).connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    meter = window.setInterval(() => {
      if (st().phase !== 'recording') return st().level && st().set({ level: 0 })
      analyser.getFloatTimeDomainData(buf)
      let sum = 0
      for (const v of buf) sum += v * v
      // RMS on a rough log scale, so speech fills the middle of the meter.
      const rms = Math.sqrt(sum / buf.length)
      const level = Math.max(0, Math.min(1, (20 * Math.log10(rms + 1e-6) + 55) / 50))
      st().set({ level })
    }, 80)
  } catch {
    // A level meter is a convenience; recording works without it.
  }
}

function release() {
  if (tick !== null) window.clearInterval(tick)
  if (meter !== null) window.clearInterval(meter)
  tick = meter = null
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  void audio?.close().catch(() => {})
  audio = null
  window.removeEventListener('beforeunload', onBeforeUnload)
}

/** Ask for the microphone and start recording (call it from a click). */
export async function start(): Promise<void> {
  if (mr || !supported()) {
    if (!supported()) st().set({ error: tr('recordings.recorder.thisBrowserCannotRecordAudio') })
    return
  }
  st().set({ phase: 'asking', error: null, blob: null, recovered: false, progress: null })
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
  } catch (e) {
    st().set({ phase: 'idle', error: micError(e) })
    return
  }
  mime = pickMime()
  try {
    mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
  } catch (e) {
    release()
    st().set({ phase: 'idle', error: e instanceof Error ? e.message : tr('recordings.recorder.thisBrowserCannotRecordAudio') })
    return
  }
  mime = mr.mimeType || mime || 'audio/webm'
  chunks = []
  seq = 0
  bytes = 0
  accMs = 0
  const id = vault.newSessionId()
  const startedAt = Date.now()
  void vault.putSession({ id, startedAt, heartbeat: startedAt, durationMs: 0, bytes: 0, mime, stopped: false, form: st().form })
  mr.ondataavailable = (ev) => {
    if (!ev.data || !ev.data.size) return
    chunks.push(ev.data)
    bytes += ev.data.size
    void vault.appendChunk(id, seq++, ev.data, elapsed(), bytes)
  }
  mr.onstop = () => {
    const blob = new Blob(chunks, { type: mime })
    void vault.patchSession(id, { stopped: true, durationMs: accMs, bytes: blob.size, heartbeat: Date.now() })
    mr = null
    release()
    st().set({ phase: 'stopped', blob, level: 0, elapsedMs: accMs })
    stopped?.(blob)
    stopped = null
  }
  mr.onerror = (ev) => {
    const err = (ev as unknown as { error?: Error }).error
    st().set({ error: tr('recordings.recorder.recordingStoppedTheAudioSo', { message: err?.message ?? tr('recordings.recorder.theRecorderFailed') }) })
    void stop()
  }
  mr.start(TIMESLICE_MS)
  segStart = Date.now()
  st().set({ phase: 'recording', startedAt, elapsedMs: 0, sessionId: id })
  window.addEventListener('beforeunload', onBeforeUnload)
  startMeter(stream)
  tick = window.setInterval(() => st().set({ elapsedMs: elapsed() }), 250)
}

export function pause(): void {
  if (!mr || st().phase !== 'recording') return
  try {
    mr.pause()
  } catch {
    return
  }
  accMs += Date.now() - segStart
  st().set({ phase: 'paused', elapsedMs: accMs, level: 0 })
}

export function resume(): void {
  if (!mr || st().phase !== 'paused') return
  try {
    mr.resume()
  } catch {
    return
  }
  segStart = Date.now()
  st().set({ phase: 'recording' })
}

/** Stop and join the audio into one file. */
export function stop(): Promise<Blob | null> {
  if (!mr) return Promise.resolve(st().blob)
  if (st().phase === 'recording') accMs += Date.now() - segStart
  st().set({ elapsedMs: accMs })
  return new Promise((resolve) => {
    stopped = resolve
    try {
      mr!.stop()
    } catch {
      resolve(null)
    }
  })
}

/** Throw the recording away (also from IndexedDB) and clear the form. */
export async function discard(): Promise<void> {
  const id = st().sessionId
  if (mr) {
    stopped = null
    mr.onstop = null
    mr.ondataavailable = null
    try {
      mr.stop()
    } catch {
      // already stopped
    }
    mr = null
  }
  release()
  if (id) await vault.deleteSession(id)
  st().set({ phase: 'idle', blob: null, sessionId: null, startedAt: null, elapsedMs: 0, level: 0, error: null, progress: null, recovered: false, form: EMPTY_FORM })
  void refreshPending()
}

/** Keep the dialog's form with the session, so a recovered recording keeps its title and notes. */
export function saveForm(form: RecorderForm): void {
  const id = st().sessionId
  if (id) void vault.patchSession(id, { form })
}

export function extensionFor(type: string): string {
  if (type.includes('ogg')) return 'ogg'
  if (type.includes('mp4') || type.includes('aac')) return 'm4a'
  if (type.includes('wav')) return 'wav'
  return 'webm'
}

/** The local datetime-local value as an ISO instant ('' stays ''). */
export function localToIso(v: string): string {
  if (!v) return ''
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

export interface UploadAnswer {
  connector_id: number
  recording_id: number
  title: string
  status: string
  url: string
  unsupported_fields?: string[]
  kept_in_mailr?: string[]
}

/** POST /speakr/recordings with the audio and the form; resolves with mailr's answer. */
export function upload(blob: Blob, form: RecorderForm, startedAt: number | null, connectorId?: number): { promise: Promise<UploadAnswer>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<UploadAnswer>((resolve, reject) => {
    xhr.open('POST', `${API}/speakr/recordings`)
    xhr.withCredentials = true
    const csrf = getCsrf()
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) st().set({ progress: e.loaded / e.total })
    }
    xhr.onload = () => {
      let data: unknown = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        // not JSON (a proxy's error page)
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data as UploadAnswer)
      if (xhr.status === 401) notifyUnauthorized()
      const detail = (data as { detail?: unknown } | null)?.detail
      const msg = typeof detail === 'string' ? detail : xhr.status === 413 ? tr('recordings.recorder.theRecordingIsLargerThan') : tr('recordings.recorder.uploadFailed', { status: xhr.status })
      void codedText(data).then((text) => reject(new ApiError(xhr.status, text ?? msg, detail)))
    }
    xhr.onerror = () => reject(new ApiError(0, tr('recordings.recorder.uploadFailedNetworkErrorThe')))
    xhr.onabort = () => reject(new ApiError(0, tr('recordings.recorder.uploadCancelled')))
    const when = startedAt ? new Date(startedAt) : new Date()
    const stamp = `${when.getFullYear()}-${String(when.getMonth() + 1).padStart(2, '0')}-${String(when.getDate()).padStart(2, '0')}-${String(when.getHours()).padStart(2, '0')}${String(when.getMinutes()).padStart(2, '0')}`
    const fd = new FormData()
    fd.append('file', blob, `recording-${stamp}.${extensionFor(blob.type)}`)
    if (form.title.trim()) fd.append('title', form.title.trim())
    if (form.participants.length) fd.append('participants', JSON.stringify(form.participants))
    fd.append('meeting_date', localToIso(form.meetingDate) || when.toISOString())
    if (form.notes.trim()) fd.append('notes', form.notes)
    if (form.folder) fd.append('folder', form.folder)
    if (form.tags.length) fd.append('tags', JSON.stringify(form.tags))
    if (form.event) {
      fd.append('event_uid', form.event.uid)
      if (form.event.occurrence) fd.append('occurrence', form.event.occurrence)
    }
    if (connectorId) fd.append('connector_id', String(connectorId))
    xhr.send(fd)
  })
  return { promise, abort: () => xhr.abort() }
}

/** After a successful upload: the session leaves IndexedDB and the recorder is ready again. */
export async function finish(): Promise<void> {
  const id = st().sessionId
  if (id) await vault.deleteSession(id)
  st().set({ phase: 'idle', blob: null, sessionId: null, startedAt: null, elapsedMs: 0, progress: null, recovered: false, error: null, form: EMPTY_FORM })
  void refreshPending()
}

/** Recordings a closed tab left in IndexedDB (not this tab's, not one another tab is still writing). */
export async function refreshPending(): Promise<PendingRecording[]> {
  const mine = st().sessionId
  const now = Date.now()
  const rows = (await vault.listSessions()).filter((r) => r.id !== mine && (r.stopped || now - r.heartbeat > vault.LIVE_MS) && r.bytes > 0)
  const pending = rows
    .sort((a, b) => b.startedAt - a.startedAt)
    .map((r) => ({ id: r.id, startedAt: r.startedAt, durationMs: r.durationMs, bytes: r.bytes, title: r.form?.title ?? '' }))
  st().set({ pending })
  return pending
}

/** Load a left-behind recording into the recorder, ready to upload (the dialog opens on it). */
export async function recover(id: string): Promise<boolean> {
  if (mr || st().phase === 'recording' || st().phase === 'paused') return false
  const row = await vault.getSession(id)
  if (!row) return false
  const blob = await vault.readAudio(id, row.mime)
  if (!blob) {
    await vault.deleteSession(id)
    void refreshPending()
    return false
  }
  st().set({
    phase: 'stopped', blob, sessionId: id, startedAt: row.startedAt, elapsedMs: row.durationMs, recovered: true, error: null,
    progress: null, form: { ...EMPTY_FORM, ...(row.form ?? {}) }, dialog: true,
  })
  void vault.patchSession(id, { stopped: true })
  void refreshPending()
  return true
}

export async function forget(id: string): Promise<void> {
  await vault.deleteSession(id)
  void refreshPending()
}

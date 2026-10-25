// Dictation in the browser (agent.dictate): the microphone is asked for on the first tap only,
// MediaRecorder keeps the clip in memory, an AnalyserNode drives the level meter and the pause
// detection, and Stop (or the pause, with "Send when I stop talking") sends the clip to
// POST /assistant/transcribe. The clip is never written anywhere: it is dropped once the request
// ends. The text comes back for the user to read and edit before anything is sent.
// ACT agent.dictate @web.ask

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../../api/client'
import { useMe } from '../../api/hooks'
import { micError } from '../recordings/recorder'
import { clipName, levelOf, MAX_CLIP_MS, pickMime, silenceFeed, silenceStart, transcribeError, type SilenceState } from './dictation'
import { t as tr } from '../../i18n'

export type DictationPhase = 'idle' | 'asking' | 'listening' | 'transcribing'

/** Whether this server offers dictation (GET /me features.transcribe). */
export function useTranscribeAvailable(): boolean {
  return useMe().data?.features?.transcribe === true
}

export function recordingSupported(): boolean {
  return typeof window !== 'undefined' && typeof window.MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

interface Options {
  /** The words, and whether a pause ended the clip ("Send when I stop talking"). */
  onText: (text: string, auto: boolean) => void
  /** End the clip after a pause in speech. */
  autoStop?: boolean
  /** What the words are spoken into (dictationContext); without it the server keeps the clip local. */
  context?: string
}

export function useDictation({ onText, autoStop = false, context }: Options) {
  const [phase, setPhase] = useState<DictationPhase>('idle')
  const [level, setLevel] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const live = useRef<{
    mr: MediaRecorder | null; stream: MediaStream | null; audio: AudioContext | null; timer: number | null
    chunks: Blob[]; startedAt: number; silence: SilenceState; auto: boolean; cancelled: boolean; mime: string
  } | null>(null)
  const onTextRef = useRef(onText)
  const autoRef = useRef(autoStop)
  const contextRef = useRef(context)
  const mounted = useRef(true)
  useEffect(() => {
    onTextRef.current = onText
    autoRef.current = autoStop
    contextRef.current = context
  })

  const release = useCallback(() => {
    const l = live.current
    if (!l) return
    if (l.timer !== null) window.clearInterval(l.timer)
    l.timer = null
    l.stream?.getTracks().forEach((t) => t.stop())
    l.stream = null
    void l.audio?.close().catch(() => {})
    l.audio = null
    if (mounted.current) setLevel(0)
  }, [])

  const upload = useCallback(async (blob: Blob, ms: number, auto: boolean) => {
    if (mounted.current) setPhase('transcribing')
    try {
      const fd = new FormData()
      fd.append('file', blob, clipName(blob.type))
      fd.append('duration_ms', String(Math.round(ms)))
      if (contextRef.current) fd.append('context', contextRef.current)
      // Empty text: silence, or only a phrase the speech model makes up from noise
      // ({dropped: "no speech"}, agent/transcribe.py).
      const r = await api.post<{ text: string; dropped?: string }>('/assistant/transcribe', fd)
      const text = (r.text ?? '').trim()
      if (!mounted.current) return
      if (text) onTextRef.current(text, auto)
      else setError(tr('ask.useDictation.noSpeechHeardTryAgain'))
    } catch (e) {
      if (mounted.current) setError(e instanceof ApiError ? transcribeError(e.status, e.message) : tr('ask.useDictation.theSpeechServerCouldNot'))
    } finally {
      if (mounted.current) setPhase('idle')
    }
  }, [])

  /** End the clip: transcribe it (unless cancelled). */
  const stop = useCallback((auto = false) => {
    const l = live.current
    if (!l?.mr || l.mr.state === 'inactive') return
    l.auto = auto
    l.mr.stop()
  }, [])

  const cancel = useCallback(() => {
    const l = live.current
    if (!l) return
    l.cancelled = true
    if (l.mr && l.mr.state !== 'inactive') l.mr.stop()
    else release()
    live.current = null
    if (mounted.current) setPhase('idle')
  }, [release])

  const start = useCallback(async () => {
    if (live.current) return
    setError(null)
    if (!recordingSupported()) {
      setError(typeof window !== 'undefined' && !window.isSecureContext ? tr('ask.useDictation.dictationNeedsASecureHttps') : tr('ask.useDictation.thisBrowserCannotRecordAudio'))
      return
    }
    setPhase('asking')
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    } catch (e) {
      if (mounted.current) {
        setPhase('idle')
        setError(micError(e))
      }
      return
    }
    if (!mounted.current) {
      stream.getTracks().forEach((t) => t.stop())
      return
    }
    const mime = pickMime((m) => typeof MediaRecorder.isTypeSupported !== 'function' || MediaRecorder.isTypeSupported(m))
    let mr: MediaRecorder
    try {
      mr = new MediaRecorder(stream, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 32_000 })
    } catch {
      stream.getTracks().forEach((t) => t.stop())
      setPhase('idle')
      setError(tr('ask.useDictation.thisBrowserCannotRecordAudio'))
      return
    }
    const now = Date.now()
    const l = { mr, stream, audio: null as AudioContext | null, timer: null as number | null, chunks: [] as Blob[], startedAt: now, silence: silenceStart(now), auto: false, cancelled: false, mime: mr.mimeType || mime || 'audio/webm' }
    live.current = l
    mr.ondataavailable = (e) => {
      if (e.data?.size) l.chunks.push(e.data)
    }
    mr.onstop = () => {
      const ms = Date.now() - l.startedAt
      release()
      if (live.current === l) live.current = null
      if (l.cancelled) return
      const blob = new Blob(l.chunks, { type: l.mime })
      l.chunks = []
      if (blob.size < 100) {
        if (mounted.current) {
          setPhase('idle')
          setError(tr('ask.useDictation.nothingWasRecordedTryAgain'))
        }
        return
      }
      void upload(blob, ms, l.auto)
    }
    // The meter and the pause; recording works without them.
    let read: (() => number) | null = null
    try {
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (Ctx) {
        l.audio = new Ctx()
        const analyser = l.audio.createAnalyser()
        analyser.fftSize = 1024
        l.audio.createMediaStreamSource(stream).connect(analyser)
        const buf = new Float32Array(analyser.fftSize)
        read = () => {
          analyser.getFloatTimeDomainData(buf)
          return levelOf(buf)
        }
      }
    } catch {
      read = null
    }
    l.timer = window.setInterval(() => {
      const t = Date.now()
      const ms = t - l.startedAt
      if (mounted.current) setElapsed(ms)
      if (ms >= MAX_CLIP_MS) {
        stop(false)
        return
      }
      if (!read) return
      const v = read()
      if (mounted.current) setLevel(v)
      const r = silenceFeed(l.silence, v, t)
      l.silence = r.state
      if (!autoRef.current) return
      if (r.verdict === 'stop') stop(true)
      else if (r.verdict === 'nothing') {
        cancel()
        if (mounted.current) setError(tr('ask.useDictation.noSpeechHeardTryAgain'))
      }
    }, 80)
    mr.start()
    setElapsed(0)
    setPhase('listening')
  }, [release, upload, stop, cancel])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      const l = live.current
      if (l) {
        l.cancelled = true
        if (l.mr && l.mr.state !== 'inactive') l.mr.stop()
        l.stream?.getTracks().forEach((t) => t.stop())
        if (l.timer !== null) window.clearInterval(l.timer)
        void l.audio?.close().catch(() => {})
        live.current = null
      }
    }
  }, [])

  /** The mic button: start, or stop and transcribe. */
  const toggle = useCallback(() => {
    if (phase === 'listening') stop(false)
    else if (phase === 'idle') void start()
  }, [phase, start, stop])

  return { phase, level, elapsed, error, setError, start, stop, cancel, toggle }
}

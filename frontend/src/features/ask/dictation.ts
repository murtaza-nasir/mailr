// The pure parts of dictation on the web (agent.dictate): the clip format the browser
// can record, the level of a buffer of samples, and the pause that ends "Send when I stop talking".
// The clip goes to mailr (POST /assistant/transcribe), which passes it to the user's own speech
// server. Chrome records WebM/Opus, Firefox Ogg/Opus, Safari MP4/AAC; WhisperX reads all three
// as they are, so nothing converts them. The browser's own speech recognition is never used: it
// sends the audio to Google or Apple.

/** Preferred first; the first one the browser can record wins. */
import { t } from '../../i18n'
import type { AskAbout } from '../assistant-context/askAbout'

export const DICTATION_MIMES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/ogg', 'audio/mp4']

/** The server's limit is 120 s; the client stops a little before it. */
export const MAX_CLIP_MS = 115_000

export function pickMime(can: (m: string) => boolean): string {
  return DICTATION_MIMES.find((m) => {
    try {
      return can(m)
    } catch {
      return false
    }
  }) ?? ''
}

/** A file name the speech server can tell the format from. */
export function clipName(mime: string): string {
  const base = mime.split(';')[0].trim().toLowerCase()
  if (base === 'audio/ogg') return 'dictation.ogg'
  if (base === 'audio/mp4' || base === 'audio/aac' || base === 'audio/x-m4a') return 'dictation.m4a'
  return 'dictation.webm'
}

/** RMS of the samples on a rough log scale, 0 (quiet) to 1, so speech sits in the middle. */
export function levelOf(samples: ArrayLike<number>): number {
  if (!samples.length) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
  const rms = Math.sqrt(sum / samples.length)
  return Math.max(0, Math.min(1, (20 * Math.log10(rms + 1e-6) + 55) / 50))
}

export interface SilenceOptions {
  /** A level at or above this is speech. */
  threshold: number
  /** This long below the threshold after speech ends the clip. */
  silenceMs: number
  /** Speech must last this long before a pause counts (a click or a cough does not). */
  minSpeechMs: number
  /** Nothing said for this long: give up. */
  noSpeechMs: number
}

export const SILENCE_DEFAULTS: SilenceOptions = { threshold: 0.3, silenceMs: 1500, minSpeechMs: 300, noSpeechMs: 8000 }

export interface SilenceState {
  startedAt: number
  lastAt: number | null
  lastLoudAt: number | null
  speechMs: number
}

export type SilenceVerdict = 'listening' | 'speaking' | 'stop' | 'nothing'

export function silenceStart(now: number): SilenceState {
  return { startedAt: now, lastAt: null, lastLoudAt: null, speechMs: 0 }
}

/**
 * One level reading. `stop` once the speaker has talked for a while and then paused for
 * `silenceMs`; `nothing` when no speech came within `noSpeechMs`.
 */
export function silenceFeed(s: SilenceState, level: number, now: number, o: SilenceOptions = SILENCE_DEFAULTS): { state: SilenceState; verdict: SilenceVerdict } {
  const dt = s.lastAt === null ? 0 : Math.max(0, now - s.lastAt)
  const loud = level >= o.threshold
  const next: SilenceState = {
    ...s,
    lastAt: now,
    lastLoudAt: loud ? now : s.lastLoudAt,
    speechMs: loud ? s.speechMs + dt : s.speechMs,
  }
  if (loud) return { state: next, verdict: 'speaking' }
  if (next.speechMs >= o.minSpeechMs && next.lastLoudAt !== null && now - next.lastLoudAt >= o.silenceMs) return { state: next, verdict: 'stop' }
  if (next.speechMs < o.minSpeechMs && now - s.startedAt >= o.noSpeechMs) return { state: next, verdict: 'nothing' }
  return { state: next, verdict: 'listening' }
}

/** Dictated words added to what is already typed, with one space between. */
export function appendWords(draft: string, words: string): string {
  const w = words.trim()
  if (!w) return draft
  if (!draft.trim()) return w
  return /\s$/.test(draft) ? `${draft}${w}` : `${draft} ${w}`
}

/**
 * What the clip is spoken into, for the server's data rule (POST /assistant/transcribe `context`):
 * `chat:<id>` for a chat that exists, `note:<id>` and `task:<uid>` for a note, task or event the
 * words are about, `none` for a new chat about nothing. Anything else (a new chat about an email or a
 * recording) sends no context, so the server treats the clip as local-only.
 */
export function dictationContext(sessionId: string | null | undefined, about?: AskAbout | null, extra?: boolean): string | undefined {
  if (sessionId) return `chat:${sessionId}`
  if (extra) return undefined
  if (!about) return 'none'
  if (about.kind === 'note') return `note:${about.id}`
  if (about.kind === 'task' || about.kind === 'event') return `task:${about.uid}`
  return undefined
}

/** The error a failed transcription shows (the server's own words when it sent some). */
export function transcribeError(status: number, message: string): string {
  if (status === 404) return t('ask.dictation.dictationIsNotSetUp')
  if (status === 409) return message || t('ask.dictation.notAvailableWithLocalModels')
  if (status === 413) return message || t('ask.dictation.thatClipIsTooLong')
  if (status === 0) return t('ask.dictation.networkErrorCheckYourConnection')
  return message || t('ask.dictation.theSpeechServerCouldNot')
}

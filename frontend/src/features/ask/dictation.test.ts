// ACT-SURFACES: web.ask
// agent.dictate on the web: the clip format and name, the level meter, the pause that ends
// "Send when I stop talking", and how dictated words join what is typed.
import { describe, expect, it } from 'vitest'
import { appendWords, clipName, dictationContext, levelOf, pickMime, SILENCE_DEFAULTS, silenceFeed, silenceStart, transcribeError, type SilenceState } from './dictation'

/** Feeds a level every 80 ms from t0 for the given spans; returns the first verdict that is not listening/speaking. */
function run(spans: [level: number, ms: number][], t0 = 1000): { verdict: string; at: number } {
  let s: SilenceState = silenceStart(t0)
  let t = t0
  for (const [level, ms] of spans) {
    for (let k = 0; k < ms; k += 80) {
      t += 80
      const r = silenceFeed(s, level, t)
      s = r.state
      if (r.verdict === 'stop' || r.verdict === 'nothing') return { verdict: r.verdict, at: t - t0 }
    }
  }
  return { verdict: 'listening', at: t - t0 }
}

describe('agent.dictate: pause detection', () => {
  it('stops 1.5 s after speech ends', () => {
    const r = run([[0.05, 400], [0.6, 2000], [0.05, 3000]])
    expect(r.verdict).toBe('stop')
    // 400 ms quiet, 2 s speech, then 1.5 s of quiet (one reading of slack).
    expect(r.at).toBeGreaterThanOrEqual(400 + 2000 + 1500)
    expect(r.at).toBeLessThanOrEqual(400 + 2000 + 1500 + 160)
  })

  it('keeps listening through short pauses between words', () => {
    expect(run([[0.6, 1000], [0.05, 1000], [0.6, 1000], [0.05, 1200]]).verdict).toBe('listening')
  })

  it('ignores a click: speech must last before a pause counts', () => {
    const r = run([[0.9, 80], [0.05, 3000]])
    expect(r.verdict).toBe('listening')
  })

  it('gives up when nothing is said', () => {
    const r = run([[0.05, 10_000]])
    expect(r.verdict).toBe('nothing')
    expect(r.at).toBeGreaterThanOrEqual(SILENCE_DEFAULTS.noSpeechMs)
  })

  it('never gives up once the speaker has started', () => {
    expect(run([[0.6, 500], [0.05, 1000], [0.6, 500], [0.05, 1000], [0.6, 7000]]).verdict).toBe('listening')
  })
})

describe('agent.dictate: clip format', () => {
  it('prefers Opus in WebM, then Ogg (Firefox), then MP4 (Safari)', () => {
    expect(pickMime(() => true)).toBe('audio/webm;codecs=opus')
    expect(pickMime((m) => m.startsWith('audio/ogg'))).toBe('audio/ogg;codecs=opus')
    expect(pickMime((m) => m === 'audio/mp4')).toBe('audio/mp4')
    expect(pickMime(() => false)).toBe('')
    expect(pickMime(() => { throw new Error('no') })).toBe('')
  })

  it('names the clip after its format', () => {
    expect(clipName('audio/webm;codecs=opus')).toBe('dictation.webm')
    expect(clipName('audio/ogg; codecs=opus')).toBe('dictation.ogg')
    expect(clipName('audio/mp4')).toBe('dictation.m4a')
    expect(clipName('')).toBe('dictation.webm')
  })

  it('reads silence as 0 and a loud signal near 1', () => {
    expect(levelOf(new Float32Array(1024))).toBe(0)
    expect(levelOf([])).toBe(0)
    const loud = Float32Array.from({ length: 1024 }, (_, i) => 0.5 * Math.sin(i / 4))
    expect(levelOf(loud)).toBeGreaterThan(0.75)
    const speech = Float32Array.from({ length: 1024 }, (_, i) => 0.05 * Math.sin(i / 4))
    expect(levelOf(speech)).toBeGreaterThan(SILENCE_DEFAULTS.threshold)
    const room = Float32Array.from({ length: 1024 }, (_, i) => 0.002 * Math.sin(i / 4))
    expect(levelOf(room)).toBeLessThan(SILENCE_DEFAULTS.threshold)
  })
})

describe('agent.dictate: the words', () => {
  it('adds dictated words after what is typed, with one space', () => {
    expect(appendWords('', ' move it to Friday ')).toBe('move it to Friday')
    expect(appendWords('Please', 'move it')).toBe('Please move it')
    expect(appendWords('Please ', 'move it')).toBe('Please move it')
    expect(appendWords('Keep', '   ')).toBe('Keep')
  })

  it('explains a failed transcription plainly', () => {
    expect(transcribeError(404, 'x')).toMatch(/not set up/)
    expect(transcribeError(413, 'Clips can be up to 120 seconds.')).toBe('Clips can be up to 120 seconds.')
    expect(transcribeError(0, '')).toMatch(/Network/)
    expect(transcribeError(502, '')).toMatch(/couldn't transcribe/i)
    expect(transcribeError(409, '')).toMatch(/local models only/)
    expect(transcribeError(409, 'Server words.')).toBe('Server words.')
  })

  it('names what the clip is spoken into, and sends nothing when it cannot tell', () => {
    expect(dictationContext('s1')).toBe('chat:s1')
    expect(dictationContext(null)).toBe('none')
    expect(dictationContext(null, { kind: 'note', id: 'n1', title: 'x', list: false })).toBe('note:n1')
    expect(dictationContext(null, { kind: 'task', uid: 'u1', title: 'x' })).toBe('task:u1')
    expect(dictationContext(null, { kind: 'conversation', id: 3, title: 'x' })).toBeUndefined()
    expect(dictationContext(null, null, true)).toBeUndefined()
  })
})

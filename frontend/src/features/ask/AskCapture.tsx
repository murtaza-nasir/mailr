// The ask capture on the web (web.ask): a small card with the item as a removable
// chip, one field for typed and dictated words, the microphone, "Send when I stop talking",
// Open chat and Send. Send opens the assistant panel and sends the words there with the item as
// structured context, so the reply and any approvals show in the chat as usual. Ask never handles
// a reminder. Escape or a click outside closes the card; the words stay until they are sent.
// ACT agent.ask_about @web.ask, ACT agent.dictate @web.ask

import { CalendarDays, CircleCheck, ListChecks, Mail, Mic, MicOff, Play, ShieldCheck, Square, StickyNote, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { askLabel, type AskAbout } from '../assistant-context/askAbout'
import { useAssistantName } from '../rail/assistant/useAssistantName'
import { appendWords, dictationContext } from './dictation'
import { useAskCapture, type AskAnchor } from './askStore'
import { useDictation, useTranscribeAvailable } from './useDictation'
import './ask.css'
import { t } from '../../i18n'

function AboutIcon({ about }: { about: AskAbout }) {
  if (about.kind === 'event') return <CalendarDays size={14} aria-hidden />
  if (about.kind === 'task') return <CircleCheck size={14} aria-hidden />
  if (about.kind === 'tasks') return <ListChecks size={14} aria-hidden />
  if (about.kind === 'recording') return <Play size={14} aria-hidden />
  if (about.kind === 'approval') return <ShieldCheck size={14} aria-hidden />
  if (about.kind === 'note') return <StickyNote size={14} aria-hidden />
  return <Mail size={14} aria-hidden />
}

const fmt = (ms: number) => {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Where the card goes: under (or over) the button that opened it, else high in the middle. */
function place(anchor: AskAnchor | null, w: number, h: number, vw: number, vh: number): { top: number; left: number } {
  if (!anchor) return { top: Math.max(16, Math.round(vh * 0.14)), left: Math.max(8, Math.round((vw - w) / 2)) }
  let top = anchor.bottom + 6
  if (top + h > vh - 8 && anchor.top - h - 6 > 8) top = anchor.top - h - 6
  const left = Math.max(8, Math.min(anchor.right - w, vw - w - 8))
  return { top: Math.max(8, Math.min(top, vh - h - 8)), left }
}

export function AskCapture() {
  const { about, anchor, draft, autoSend, setDraft, close, detach, setAutoSend, send, openChat } = useAskCapture()
  const aName = useAssistantName()
  const canDictate = useTranscribeAvailable()
  const ref = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const phone = typeof window !== 'undefined' && window.innerWidth < 600
  const dict = useDictation({
    autoStop: autoSend,
    context: dictationContext(null, about),
    onText: (text, auto) => {
      const next = appendWords(useAskCapture.getState().draft, text)
      useAskCapture.getState().setDraft(next)
      if (auto && useAskCapture.getState().autoSend) useAskCapture.getState().send()
      else requestAnimationFrame(() => {
        const el = field.current
        if (!el) return
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      })
    },
  })
  const busy = dict.phase !== 'idle'

  useLayoutEffect(() => {
    if (phone) return
    const el = ref.current
    if (!el) return
    setPos(place(anchor, el.offsetWidth, el.offsetHeight, window.innerWidth, window.innerHeight))
  }, [anchor, phone])

  // Focus the words once the card is placed (a hidden card cannot take focus); give it back on close.
  const placed = phone || pos !== null
  const focused = useRef(false)
  useEffect(() => {
    if (!placed || focused.current) return
    focused.current = true
    const el = field.current
    if (!el) return
    el.focus({ preventScroll: true })
    el.setSelectionRange(el.value.length, el.value.length)
  }, [placed])
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    return () => {
      if (prev && prev !== document.body && document.contains(prev)) prev.focus({ preventScroll: true })
    }
  }, [])

  const busyRef = useRef(busy)
  useEffect(() => {
    busyRef.current = busy
  })
  useEffect(() => {
    // On the window, so the card closes before a sheet or a menu under it hears the key.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      e.preventDefault()
      close()
    }
    // A click outside closes the card, but not while the microphone is on or the words are on their way.
    const onDown = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node)) return
      if (useAskCapture.getState().open && !busyRef.current) close()
    }
    window.addEventListener('keydown', onKey, true)
    document.addEventListener('mousedown', onDown, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      document.removeEventListener('mousedown', onDown, true)
    }
  }, [close])

  const grow = () => {
    const el = field.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(180, el.scrollHeight)}px`
  }
  useLayoutEffect(grow, [draft])

  const label = about ? askLabel(about) : null
  const style: CSSProperties | undefined = phone ? undefined : { top: pos?.top ?? -9999, left: pos?.left ?? -9999 }

  return createPortal(
    <>
      {phone && <div className="ask-scrim" aria-hidden />}
      <div ref={ref} className={`ask-card${phone ? ' sheet' : ''}`} role="dialog" aria-modal="false" aria-label={t('ask.askCapture.ask', { name: aName })} data-testid="ask-capture" style={style}>
        <div className="ask-head">
          <span className="ask-title">{t('ask.askCapture.ask', { name: aName })}</span>
          <span className="spacer" />
          <button type="button" className="icon-btn xs" aria-label={t('common.close')} data-tip={t('common.closeEsc')} onClick={close}>
            <X size={16} />
          </button>
        </div>
        <div className="ask-about">
          {about ? (
            <span className="ask-chip" data-testid="ask-chip" data-tip={t('ask.askCapture.readsThisWithYourWords', { name: aName })}>
              <AboutIcon about={about} />
              <span className="ellipsis">{label}</span>
              <button type="button" className="ask-chip-x" aria-label={t('ask.askCapture.remove', { label })} onClick={detach}>
                <X size={12} />
              </button>
            </span>
          ) : (
            <span className="ask-none">{t('ask.askCapture.noItemAttached')}</span>
          )}
        </div>
        <textarea
          ref={field}
          className="ask-field"
          rows={2}
          value={draft}
          aria-label={t('ask.askCapture.yourWordsFor', { name: aName })}
          placeholder={canDictate ? t('ask.askCapture.typeOrTapTheMicrophone') : t('ask.askCapture.whatShouldDo', { name: aName })}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
          disabled={dict.phase === 'transcribing'}
        />
        {(dict.phase !== 'idle' || dict.error) && (
          <div className={`ask-status${dict.error ? ' error' : ''}`} role={dict.error ? 'alert' : 'status'} aria-live="polite">
            {dict.phase === 'asking' && <span>{t('ask.askCapture.waitingForTheMicrophone')}</span>}
            {dict.phase === 'listening' && (
              <>
                <span className="ask-rec-dot" aria-hidden />
                <span>{autoSend ? t('ask.askCapture.listeningSendsWhenYouStop') : t('ask.askCapture.listeningTapStopWhenYou')}</span>
                <span className="ask-meter" aria-hidden><span style={{ transform: `scaleX(${Math.max(0.04, dict.level)})` }} /></span>
                <span className="ask-time">{fmt(dict.elapsed)}</span>
              </>
            )}
            {dict.phase === 'transcribing' && (
              <>
                <span className="spinner sm" aria-hidden /> <span>{t('ask.askCapture.transcribing')}</span>
              </>
            )}
            {dict.phase === 'idle' && dict.error && <span>{dict.error}</span>}
          </div>
        )}
        <div className="ask-actions">
          {canDictate && (
            <>
              <button
                type="button"
                className={`ask-mic${dict.phase === 'listening' ? ' on' : ''}`}
                aria-label={dict.phase === 'listening' ? t('ask.askCapture.stopDictation') : t('ask.askCapture.dictate')}
                aria-pressed={dict.phase === 'listening'}
                data-tip={dict.phase === 'listening' ? t('common.stop') : t('ask.askCapture.dictate')}
                data-testid="ask-mic"
                disabled={dict.phase === 'asking' || dict.phase === 'transcribing'}
                onClick={dict.toggle}
              >
                {dict.phase === 'listening' ? <Square size={14} /> : dict.error?.includes('icrophone') ? <MicOff size={18} /> : <Mic size={18} />}
              </button>
              <label className="ask-auto">
                <input type="checkbox" className="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} />
                <span>{t('ask.askCapture.sendWhenIStopTalking')}</span>
              </label>
            </>
          )}
          <span className="spacer" />
          <button type="button" className="btn text sm" onClick={openChat} disabled={busy}>
            {t('ask.askCapture.openChat')}
          </button>
          <button type="button" className="btn filled sm" onClick={() => send()} disabled={!draft.trim() || busy}>
            {t('common.send')}
          </button>
        </div>
      </div>
    </>,
    document.body,
  )
}

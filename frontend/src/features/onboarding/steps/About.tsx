import { assistantNameNow } from '../../rail/assistant/useAssistantName'
// Step 8 (optional): "About you". Two ways to fill the assistant's About-you profile: a few
// simple fields, or a short chat with the assistant (one question at a time). The chat ends with a
// summary she can edit; nothing is saved until she approves it (POST /onboarding/about).

import { CheckCircle2, ListChecks, MessageCircleHeart, RotateCcw, Send, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../../api/client'
import { useMe } from '../../../api/hooks'
import { Spinner } from '../../../components/bits'
import { toast, toastError } from '../../../stores/ui'
import { chatApi, type ChatTurn } from '../api'
import { EMPTY_FIELDS, TONES, draftFromSummary, draftToSave, fieldsToSave, type AboutFields, type AboutSave, type SummaryDraft, type Tone } from '../aboutLogic'
import { ChoiceCard, StepFrame } from '../guide'
import { useGuide } from '../guideContext'
import { firstName } from './Welcome'
import { scrollBehavior } from '../../../lib/motion'
import { t as tr, tx } from '../../../i18n'

type Mode = 'choose' | 'fields' | 'chat' | 'review' | 'saved'

const KIND_WORDS: Record<string, string> = { preference: tr('onboarding.steps.about.preference'), fact: tr('onboarding.steps.about.aboutYou'), habit: tr('onboarding.steps.about.habit'), relationship: tr('common.people'), commitment: tr('onboarding.steps.about.toDo'), status: tr('common.status') }

function Fields({ value, onChange }: { value: AboutFields; onChange: (f: AboutFields) => void }) {
  const set = <K extends keyof AboutFields>(k: K, v: AboutFields[K]) => onChange({ ...value, [k]: v })
  return (
    <div className="ob-form" data-testid="about-fields">
      <div className="field ob-field">
        <label htmlFor="ab-name">{tr('onboarding.steps.about.whatShouldCallYou', { name: assistantNameNow() })}</label>
        <input id="ab-name" className="input ob-input" value={value.name} onChange={(e) => set('name', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="ab-role">{tr('onboarding.steps.about.whatDoYouDo')}</label>
        <input id="ab-role" className="input ob-input" placeholder={tr('onboarding.steps.about.eGTeacherRetiredNurse')} value={value.role} onChange={(e) => set('role', e.target.value)} />
      </div>
      <div className="field ob-field">
        <span className="ob-label" id="ab-tone">{tr('onboarding.steps.about.howDoYouLikeYour')}</span>
        <div className="ob-chips" role="group" aria-labelledby="ab-tone">
          {TONES.map((t: Tone) => {
            const on = value.tones.includes(t)
            return (
              <button key={t} type="button" aria-pressed={on} className={`ob-chip${on ? ' on' : ''}`} onClick={() => set('tones', on ? value.tones.filter((x) => x !== t) : [...value.tones, t])}>
                {t}
              </button>
            )
          })}
        </div>
        <input className="input ob-input" aria-label={tr('onboarding.steps.about.anythingElseAboutHowYou')} placeholder={tr('onboarding.steps.about.anythingElseEGI')} value={value.writing} onChange={(e) => set('writing', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="ab-signoff">{tr('onboarding.steps.about.howDoYouSignOff')}</label>
        <input id="ab-signoff" className="input ob-input" placeholder={tr('onboarding.steps.about.eGLoveRosa')} value={value.signoff} onChange={(e) => set('signoff', e.target.value)} />
        <span className="ob-hint">{tr('onboarding.steps.about.thisAlsoBecomesYourEmail')}</span>
      </div>
      <div className="field ob-field">
        <label htmlFor="ab-notes">{tr('onboarding.steps.about.anythingShouldKnowOrAvoid', { name: assistantNameNow() })}</label>
        <textarea id="ab-notes" className="textarea ob-input" rows={3} placeholder={tr('onboarding.steps.about.eGSamIsMy')} value={value.notes} onChange={(e) => set('notes', e.target.value)} />
      </div>
    </div>
  )
}

function Chat({ name, onSummary, onFallback }: { name: string; onSummary: (d: SummaryDraft, turns: ChatTurn[]) => void; onFallback: (note: string) => void }) {
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(true)
  const [done, setDone] = useState(false)
  const log = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  // 403: the policy's message; 503: the server's gentle note; anything else: the same note.
  const fail = (e: unknown) => {
    const err = e as ApiError
    onFallback(err.status === 403 || err.status === 503 ? err.message : tr('onboarding.steps.about.cantChatRightNowYou', { name: assistantNameNow() }))
  }

  useEffect(() => {
    let live = true
    chatApi
      .turn([])
      .then((r) => {
        if (!live) return
        setTurns([{ role: 'assistant', content: r.reply }])
        setBusy(false)
      })
      .catch((e) => live && fail(e))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the chat opens
  }, [])

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: scrollBehavior() })
    if (!busy) input.current?.focus()
  }, [turns, busy])

  const send = async () => {
    const t = text.trim()
    if (!t || busy) return
    const next: ChatTurn[] = [...turns, { role: 'user', content: t }]
    setTurns(next)
    setText('')
    setBusy(true)
    try {
      const r = await chatApi.turn(next)
      setTurns([...next, { role: 'assistant', content: r.reply }])
      if (r.done) setDone(true)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const summarise = async () => {
    setBusy(true)
    try {
      const s = await chatApi.summary(turns)
      onSummary(draftFromSummary(s), turns)
    } catch (e) {
      fail(e)
      setBusy(false)
    }
  }

  const answered = turns.some((t) => t.role === 'user')
  return (
    <div className="ob-chat" data-testid="quick-chat">
      <div className="ob-chat-log" ref={log} role="log" aria-live="polite" aria-label={tr('onboarding.steps.about.chatWith', { itemName: name })}>
        {turns.map((t, i) => (
          <div key={i} className={`ob-bubble ${t.role}`}>
            {t.content}
          </div>
        ))}
        {busy && (
          <div className="ob-bubble assistant typing" aria-label={tr('onboarding.steps.about.isTyping', { itemName: name })}>
            <span />
            <span />
            <span />
          </div>
        )}
      </div>
      {done ? (
        <button type="button" className="btn filled ob-big" disabled={busy} onClick={() => void summarise()}>
          {busy ? <Spinner small /> : <Sparkles />}{' '}{tr('onboarding.steps.about.showMeWhatYouLearned')}
        </button>
      ) : (
        <form
          className="ob-chat-input"
          onSubmit={(e) => {
            e.preventDefault()
            void send()
          }}
        >
          <textarea
            ref={input}
            className="textarea ob-input"
            rows={2}
            aria-label={tr('onboarding.steps.about.yourAnswer')}
            placeholder={tr('onboarding.steps.about.typeYourAnswer')}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send()
              }
            }}
          />
          <button type="submit" className="btn filled" aria-label={tr('common.send')} disabled={busy || !text.trim()}>
            <Send />
          </button>
        </form>
      )}
      {answered && !done && (
        <button type="button" className="btn text" disabled={busy} onClick={() => void summarise()}>
          {tr('onboarding.steps.about.thatsEnoughForNowShow')}
        </button>
      )}
    </div>
  )
}

function Review({ draft, onChange }: { draft: SummaryDraft; onChange: (d: SummaryDraft) => void }) {
  const set = <K extends keyof SummaryDraft>(k: K, v: SummaryDraft[K]) => onChange({ ...draft, [k]: v })
  return (
    <div className="ob-form ob-review" data-testid="about-review">
      <p className="ob-note">
        <CheckCircle2 />
        <span>
          {tx('onboarding.steps.about.hereIsWhatLearnedChange', { name: assistantNameNow() }, { b: (c) => <b>{c}</b> })}
        </span>
      </p>
      <div className="field ob-field">
        <label htmlFor="rv-name">{tr('common.name')}</label>
        <input id="rv-name" className="input ob-input" value={draft.preferred_name} onChange={(e) => set('preferred_name', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="rv-role">{tr('onboarding.steps.about.whatYouDo')}</label>
        <input id="rv-role" className="input ob-input" value={draft.role} onChange={(e) => set('role', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="rv-about">{tr('onboarding.steps.about.aboutYou')}</label>
        <textarea id="rv-about" className="textarea ob-input" rows={3} value={draft.about} onChange={(e) => set('about', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="rv-writing">{tr('onboarding.steps.about.howYouWrite')}</label>
        <textarea id="rv-writing" className="textarea ob-input" rows={2} value={draft.writing} onChange={(e) => set('writing', e.target.value)} />
      </div>
      <div className="field ob-field">
        <label htmlFor="rv-sig">{tr('onboarding.steps.about.yourSignOffAlsoYour')}</label>
        <textarea id="rv-sig" className="textarea ob-input" rows={2} value={draft.signature} onChange={(e) => set('signature', e.target.value)} />
      </div>
      {draft.memories.length > 0 && (
        <div className="field ob-field">
          <span className="ob-label">{tr('onboarding.steps.about.thingsToRemember')}</span>
          <ul className="ob-memories">
            {draft.memories.map((m, i) => (
              <li key={i} className={m.keep ? '' : 'off'}>
                <span className="pill">{KIND_WORDS[m.kind] ?? tr('common.note')}</span>
                <input
                  className="input"
                  aria-label={tr('onboarding.steps.about.note', { value: i + 1 })}
                  value={m.text}
                  onChange={(e) => set('memories', draft.memories.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
                />
                <button type="button" className="icon-btn" aria-label={m.keep ? tr('onboarding.steps.about.leaveOutNote', { value: i + 1 }) : tr('onboarding.steps.about.keepNote', { value: i + 1 })} data-tip={m.keep ? tr('onboarding.steps.about.leaveOut') : tr('common.keep')} onClick={() => set('memories', draft.memories.map((x, j) => (j === i ? { ...x, keep: !x.keep } : x)))}>
                  {m.keep ? <Trash2 /> : <RotateCcw />}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function AboutStep() {
  const g = useGuide()
  const me = useMe().data!
  const name = g.state.assistant.name || assistantNameNow()
  const canChat = g.state.assistant.available
  // Saved before (the step is marked done when saved): back here, the step says so.
  const [mode, setMode] = useState<Mode>(g.state.steps.about === 'done' ? 'saved' : 'choose')
  const [pick, setPick] = useState<'fields' | 'chat'>(canChat ? 'chat' : 'fields')
  const [fields, setFields] = useState<AboutFields>({ ...EMPTY_FIELDS, name: firstName(me.user.name) })
  const [draft, setDraft] = useState<SummaryDraft | null>(null)
  const [note, setNote] = useState(canChat ? '' : tr('onboarding.steps.about.cantChatRightNowSo', { itemName: name }))
  const [busy, setBusy] = useState(false)

  const save = async (body: AboutSave, source: 'fields' | 'chat') => {
    setBusy(true)
    try {
      await chatApi.save({ ...body, source })
      setMode('saved')
      toast({ message: tr('onboarding.steps.about.willUseThisFromNow', { itemName: name }) })
    } catch (e) {
      toastError(e, tr('common.notSaved'))
    } finally {
      setBusy(false)
    }
  }

  const primary =
    mode === 'choose'
      ? { label: tr('common.start'), onClick: () => setMode(pick) }
      : mode === 'fields'
        ? { label: tr('common.save'), busy, onClick: () => void save(fieldsToSave(fields), 'fields') }
        : mode === 'review' && draft
          ? { label: tr('common.save'), busy, onClick: () => void save(draftToSave(draft), 'chat') }
          : mode === 'chat'
            ? { label: tr('common.continue'), disabled: true }
            : { label: tr('common.continue') }

  return (
    <StepFrame
      icon={<MessageCircleHeart />}
      title={tr('onboarding.steps.about.getToKnowYou')}
      lead={tr('onboarding.steps.about.tellYourAssistantALittle', { itemName: name })}
      skip={mode !== 'saved'}
      primary={primary}
    >
      {note && (
        <p className="ob-note" role="status" data-testid="about-note">
          {note}
        </p>
      )}
      {mode === 'choose' && (
        <div className="ob-choices" role="radiogroup" aria-label={tr('onboarding.steps.about.howToTellAboutYou', { itemName: name })}>
          <ChoiceCard on={pick === 'chat'} onSelect={() => canChat && setPick('chat')} title={tr('onboarding.steps.about.haveAQuickChat')} desc={canChat ? tr('onboarding.steps.about.asksAFewShortQuestions', { itemName: name }) : tr('onboarding.steps.about.isntAvailableRightNow', { itemName: name })} name={tr('onboarding.steps.about.haveAQuickChat')} testId="about-pick-chat">
            <MessageCircleHeart />
          </ChoiceCard>
          <ChoiceCard on={pick === 'fields'} onSelect={() => setPick('fields')} title={tr('onboarding.steps.about.fillInAFewFields')} desc={tr('onboarding.steps.about.fiveSimpleQuestionsOnOne')} name={tr('onboarding.steps.about.fillInAFewFields')} testId="about-pick-fields">
            <ListChecks />
          </ChoiceCard>
        </div>
      )}
      {mode === 'fields' && <Fields value={fields} onChange={setFields} />}
      {mode === 'chat' && (
        <Chat
          name={name}
          onSummary={(d) => {
            setDraft(d)
            setMode('review')
          }}
          onFallback={(n) => {
            setNote(n)
            setMode('fields')
          }}
        />
      )}
      {mode === 'review' && draft && (
        <>
          <Review draft={draft} onChange={setDraft} />
          <button type="button" className="btn text" onClick={() => { setDraft(null); setMode('chat') }}>
            <RotateCcw />{' '}{tr('onboarding.steps.about.startTheChatAgain')}
          </button>
        </>
      )}
      {mode === 'saved' && (
        <p className="ob-ok" data-testid="about-saved">
          <CheckCircle2 />{' '}{tr('onboarding.steps.about.savedYouCanChangeThis', { name: assistantNameNow() })}
        </p>
      )}
    </StepFrame>
  )
}

import { assistantNameNow } from './useAssistantName'
// The assistant side panel: a chat whose replies are timelines (text, tool steps, more text, the
// answer), with verified citations, approval cards, drafts, configuration changes with Undo, and
// per-message actions (copy, rerun, edit and resend, retry an interrupted turn).

import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, ArrowDown, ArrowUp, CalendarDays, Cloud, Copy, FilePen, History, IdCard, Keyboard, ListChecks, Lock, Mail, PenLine, Play,
  RefreshCw, Settings2, ShieldCheck, Sparkles, Square, SquarePen, StickyNote, Undo2, X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useLocation } from 'react-router'
import { useAmbroseNav } from './ambroseNav'
import { qk, useConversation, useMe } from '../../../api/hooks'
import type { ComposePayload } from '../../../api/types'
import { useCompose } from '../../../stores/compose'
import { toast, toastError, useUI } from '../../../stores/ui'
import { MyAvatar } from '../../../components/MyAvatar'
import { ActivityList } from './ActivityList'
import { ChatList } from './ChatList'
import { CHATS_KEY, TITLE_MAX, type ChatSummary } from './chatList'
import { useRenameChat } from './useRenameChat'
import { ApprovalCard } from './ApprovalCard'
import { ReviewProgress } from './ReviewProgress'
import { useDatePrefs } from '../../tasks/api'
import { citationRoute, citeWhen, finalText, openWebCitation, streamingText, viewOfLocation, usedCitations, sourceRows, type Citation } from './citations'
import { useRecordingSheet } from '../../speakr/store'
import { Markdown } from './Markdown'
import { useSkillPicker, useSkills } from './SkillPicker'
import { commandListFor, parseCommand, useCommands, type ChatCommand } from './commands'
import { copyText, foldRepeatGroups, thinkingNow, turnText, type AnswerModel, type StepsItem } from './timeline'
import { askLabel, askSuggestions, useAskAbout } from '../../assistant-context/askAbout'
import { appendWords, dictationContext } from '../../ask/dictation'
import { MicButton } from '../../ask/MicButton'
import { useAssistant, type ChatTitle, type ApprovalCardData, type ApprovalRef, type AssistantTurn, type DraftCard, type UserTurn } from './useAssistant'
import { api } from '../../../api/client'
import { focusOpen } from '../../../lib/live/editors'
import { useLatestVersion, useLive, type LiveKind } from '../../../lib/live/store'
import { eventPath, notePath, taskPath } from '../../../lib/routes'
import { useFollowScroll } from './useFollowScroll'
import { saveAnswerToNote } from '../../notes/fromAnswer'
import { t as tr, tx, appLocale } from '../../../i18n'
import { useAssistantName } from './useAssistantName'
import { UsedMemories } from './UsedMemories'
import { PersonCardDialog } from '../../people/PersonCardDialog'

const SUGGESTIONS_CONV = [
  tr('rail.assistant.assistantPanel.summarizeThisConversationAndList'),
  tr('rail.assistant.assistantPanel.draftAReplyThatAccepts'),
  tr('rail.assistant.assistantPanel.whatHaveIDiscussedWith'),
]
const SUGGESTIONS_GENERAL = [
  tr('rail.assistant.assistantPanel.whatNeedsAReplyFrom'),
  tr('rail.assistant.assistantPanel.findTheLatestInvoiceI'),
  tr('rail.assistant.assistantPanel.whatDeadlinesDoIHave'),
]

const STOP_NOTES: Record<string, string> = {
  rounds: tr('rail.assistant.assistantPanel.stoppedSearchingAtTheStep'),
  time: tr('rail.assistant.assistantPanel.stoppedSearchingAtTheTime'),
  size: tr('rail.assistant.assistantPanel.stoppedAfterReadingAsMuch'),
  repeats: tr('rail.assistant.assistantPanel.stoppedRepeatingTheSameSearch'),
}

function escapeHtml(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!)
}

/** Answer text → compose HTML (citation links dropped, plain paragraphs). */
/** The answer as email HTML: the same Markdown rendering as the panel, citation markers
 *  dropped, headings as bold lines (mail clients style h1–h3 unpredictably). Raw HTML in the
 *  answer is not rendered (react-markdown's default), so nothing from a model reaches the
 *  draft as markup; images keep only their alt text, never a remote URL. */
export async function answerToHtml(md: string): Promise<string> {
  const plain = md.replace(/\s*\[\d+\]\(mailr-cite:\d+\)/g, '')
  const [{ renderToStaticMarkup }, { default: ReactMarkdown }, { default: remarkGfm }] = await Promise.all([
    import('react-dom/server'), import('react-markdown'), import('remark-gfm'),
  ])
  const bold = ({ children }: { children?: ReactNode }) => (
    <p>
      <strong>{children}</strong>
    </p>
  )
  // An image in the answer would go out as a remote image (a tracking or data-carrying URL an
  // injected email can plant): only its alt text stays.
  const img = ({ alt }: { alt?: string }) => (alt ? <span>{alt}</span> : null)
  const html = renderToStaticMarkup(
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ h1: bold, h2: bold, h3: bold, h4: bold, h5: bold, h6: bold, img }}>
      {plain}
    </ReactMarkdown>,
  )
  return html || `<p>${escapeHtml(plain)}</p>`
}

const shortDateOf = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(appLocale(), { month: 'short', day: 'numeric' }) : '')

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    toast({ message: tr('rail.assistant.assistantPanel.copied') })
  } catch {
    toast({ message: tr('rail.assistant.assistantPanel.couldNotCopyToThe'), error: true })
  }
}

function Sources({ turn, onOpen }: { turn: AssistantTurn; onOpen: (c: Citation) => void }) {
  const zone = useDatePrefs().zone
  const finals = turn.items.map((i) => (i.kind === 'text' ? i.final ?? '' : '')).join('\n')
  const sources = sourceRows(usedCitations(finals, turn.citations))
  if (!sources.length) return null
  return (
    <div className="sources" aria-label={tr('rail.assistant.assistantPanel.sources')}>
      <span className="sources-label">{tr('rail.assistant.assistantPanel.sources')}</span>
      {sources.map(({ key, first, all }) => (
        <button key={key} type="button" className="source" onClick={() => onOpen(first)}>
          <span className="source-nums" aria-hidden>
            {all.map((c) => (
              <span key={c.n} className={`cite-chip static${c.verified ? '' : ' unverified'}`}>
                {c.n}
              </span>
            ))}
          </span>
          <span className="source-text">
            <span className="source-subject" dir="auto">{first.title || first.subject || tr('common.noSubject')}</span>
            <span className="source-from">{first.kind === 'web' ? [first.site, shortDateOf(first.date)].filter(Boolean).join(' · ') : first.kind === 'recording' ? [tr('common.recording'), shortDateOf(first.date)].filter(Boolean).join(' · ') : first.kind === 'note' ? [first.from, first.item_id ? first.snippet : null].filter(Boolean).join(' · ') : first.uid ? [first.from, citeWhen(first, zone)].filter(Boolean).join(' · ') : first.from}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

function StepsBlock({ item, live }: { item: StepsItem; live: boolean }) {
  return (
    <div className="chat-steps-group">
      <ActivityList steps={item.steps} running={live} />
    </div>
  )
}



interface TurnProps {
  turn: AssistantTurn
  index: number
  isLast: boolean
  conversationId: number | null
  conv?: { subject: string } | null
  onOpen: (c: Citation) => void
  onApproval: (id: number, status: string, card?: ApprovalCardData) => void
  onResume: (id: number) => void
  onReviewCard: (reviewId: number, card: ApprovalCardData) => void
  onRerun: (userMessageId: number) => void
  onUndo: (turn: number, config: number) => Promise<boolean>
}

function TurnView({ turn, index, isLast, conversationId, conv, onOpen, onApproval, onResume, onReviewCard, onRerun, onUndo }: TurnProps) {
  const openCompose = useCompose((s) => s.open)
  const qc = useQueryClient()
  const nav = useAmbroseNav()
  const shown = foldRepeatGroups(turn.items)
  const last = shown[shown.length - 1]
  const placed = new Set(turn.items.flatMap((i) => (i.kind === 'approval' ? [i.id] : [])))
  const loose: ApprovalRef[] = turn.approvals.filter((a) => !placed.has(a.id))
  const answer = turnText(turn)
  const hasText = answer.trim() !== ''
  const interrupted = turn.status === 'interrupted'
  const canRetry = !turn.running && turn.userMessageId != null && (interrupted || turn.status === 'error')
  const [card, setCard] = useState<string | null>(null)
  const lookedUp = [...new Set(turn.items.flatMap((i) => (i.kind === 'steps' ? i.steps : []))
    .filter((st) => st.name === 'get_person' && st.status === 'done')
    .map((st) => String((st.args as { email?: string } | undefined)?.email ?? '').toLowerCase())
    .filter((e) => e.includes('@')))].slice(0, 3)

  const undo = (k: number) =>
    void onUndo(index, k)
      .then((ok) => {
        if (!ok) return
        toast({ message: tr('common.changeUndone') })
        for (const key of [qk.labels, qk.me, ['rules'], ['layouts'], ['assistant']]) void qc.invalidateQueries({ queryKey: key })
      })
      .catch((e) => toastError(e, tr('common.couldNotUndo')))

  return (
    <div className={`chat-turn${turn.running ? ' running' : ''}`}>
      {shown.map((it, k) => {
        if (it.kind === 'steps') {
          return <StepsBlock key={`s${it.segment}-${k}`} item={it} live={turn.running && it === last} />
        }
        if (it.kind === 'approval') {
          const a = turn.approvals.find((x) => x.id === it.id)
          return a ? <ApprovalCard key={`a${it.id}`} a={a} onStatus={onApproval} onDecided={(id) => onResume(id)} /> : null
        }
        if (it.kind === 'resume') {
          return (
            <div key={`r${k}`} className="chat-resume" role="separator">
              <span>{it.text}</span>
            </div>
          )
        }
        const text = it.final != null ? finalText(it.final) : streamingText(it.raw)
        if (!text.trim()) return null
        const streaming = it.final === null && turn.running
        // Citations work while the answer streams: each link arrives with its citation.
        return (
          <div key={`t${it.segment}-${k}`} className={`chat-msg assistant${streaming ? ' streaming' : ''}${it !== last ? ' interim' : ''}`}>
            <Markdown text={text} citations={turn.citations} onOpenCitation={onOpen} onOpenAppLink={nav} />
          </div>
        )
      })}
      {(turn.reviews ?? []).map((rid) => <ReviewProgress key={`rv${rid}`} id={rid} onCard={onReviewCard} />)}
      {loose.map((a) => (
        <ApprovalCard key={a.id} a={a} onStatus={onApproval} onDecided={(id) => onResume(id)} />
      ))}
      {thinkingNow(turn) && (
        <span className="thinking" role="status">
          <span className="spinner xs" aria-hidden />{' '}{tr('rail.assistant.assistantPanel.thinking')}
        </span>
      )}
      {(turn.draftEdits.length > 0 || turn.configs.length > 0) && (
        <ul className="chat-changes" aria-label={tr('rail.assistant.assistantPanel.changesMade', { name: assistantNameNow() })}>
          {turn.draftEdits.map((d, k) => (
            <li key={`d${k}`}>
              <PenLine size={14} aria-hidden />
              <span className="grow">{d.applied ? tr('rail.assistant.assistantPanel.editedYourOpenDraft', { summary: d.summary }) : tr('rail.assistant.assistantPanel.theDraftWindowWasClosed')}</span>
              {d.applied && <span className="muted">{tr('rail.assistant.assistantPanel.undoItInTheDraft')}</span>}
            </li>
          ))}
          {turn.configs.map((c, k) => (
            <li key={`c${k}`} className={c.undone ? 'undone' : ''}>
              <Settings2 size={14} aria-hidden />
              <span className="grow">{c.undone ? tr('rail.assistant.assistantPanel.undone', { title: c.title }) : c.title}</span>
              {c.undo && !c.undone && (
                <button type="button" className="btn text sm" onClick={() => undo(k)}>
                  <Undo2 size={14} />{' '}{tr('common.undo')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {turn.error && <div className="chat-msg assistant error" dir="auto">{turn.error}</div>}
      {card && <PersonCardDialog email={card} onClose={() => setCard(null)} />}
      {interrupted && (
        <div className="chat-interrupted" role="status">
          <AlertTriangle size={16} aria-hidden />
          <span>{tr('rail.assistant.assistantPanel.interrupted', { value: hasText ? tr('rail.assistant.assistantPanel.theReplyAboveIsIncomplete') : tr('rail.assistant.assistantPanel.noReplyWasReceived') })}</span>
        </div>
      )}
      {turn.stopped && !turn.error && !interrupted && <div className="muted stopped-note">{tr('rail.assistant.assistantPanel.stopped')}</div>}
      {!turn.running && turn.unconfirmed && (
        <div className="chat-interrupted" role="status" data-testid="unconfirmed-claim">
          <AlertTriangle size={16} aria-hidden />
          <span>{tr('rail.assistant.assistantPanel.unconfirmedClaim')}</span>
        </div>
      )}
      {!turn.running && turn.stopReason && STOP_NOTES[turn.stopReason] && (
        <div className="muted stopped-note">{STOP_NOTES[turn.stopReason]}</div>
      )}
      {!turn.running && <Sources turn={turn} onOpen={onOpen} />}
      {!turn.running && !!turn.memoryIds?.length && <UsedMemories ids={turn.memoryIds} />}
      {!turn.running && (
        <div className="row chat-actions">
          {turn.drafts.map((d, j) => (
            <DraftCardButton key={d.draftId ?? `n${j}`} card={d} n={turn.drafts.length > 1 ? j + 1 : null} conversationId={conversationId} />
          ))}
          {turn.refs.filter((r) => r.kind === 'note').map((r) => (
            // The note or list the turn made or changed (its card opens it).
            <button key={`note:${r.id}`} type="button" className="btn tonal sm" data-testid="open-note-ref" onClick={() => nav(notePath(String(r.id)))}>
              <StickyNote size={16} />{' '}{tr('rail.assistant.assistantPanel.open', { value: r.title ? `“${r.title.length > 32 ? `${r.title.slice(0, 31)}…` : r.title}”` : 'note' })}
            </button>
          ))}
          {turn.refs.filter((r) => r.kind === 'event' || r.kind === 'task').map((r) => (
            // The event or task the turn made or changed (its card opens it).
            <button key={`${r.kind}:${r.id}`} type="button" className="btn tonal sm" onClick={() => nav(r.kind === 'event' ? eventPath(String(r.id), '') : taskPath(String(r.id)))}>
              {r.kind === 'event' ? <CalendarDays size={16} /> : <ListChecks size={16} />}{' '}{tr('rail.assistant.assistantPanel.open2', { kind: r.kind })}
              <UpdatedSince kind={r.kind} id={r.id} version={r.version} />
            </button>
          ))}
          {lookedUp.map((email) => (
            // A person Ambrose looked up: their card (details, mail, What Ambrose knows).
            <button key={`person:${email}`} type="button" className="btn tonal sm" data-testid="open-person-card" onClick={() => setCard(email)}>
              <IdCard size={16} />{' '}{tr('rail.assistant.assistantPanel.personCard', { who: email })}
            </button>
          ))}
          {canRetry && (
            <button type="button" className="btn tonal sm" onClick={() => onRerun(turn.userMessageId!)}>
              <RefreshCw size={16} />{' '}{tr('common.retry')}
            </button>
          )}
          {isLast && turn.drafts.length === 0 && turn.refs.length === 0 && turn.approvals.length === 0 && turn.draftEdits.length === 0 && turn.configs.length === 0 && conv && hasText && !turn.error && (
            <button
              type="button"
              className="btn text sm"
              onClick={async () => {
                const payload: ComposePayload = {
                  identity_id: null, to: [], cc: [], bcc: [], subject: tr('rail.assistant.assistantPanel.re', { subject: conv.subject }),
                  html: await answerToHtml(answer), attachments: [],
                }
                openCompose({ payload, conversationId })
                toast({ message: tr('rail.assistant.assistantPanel.draftOpenedReviewItBefore') })
              }}
            >
              <FilePen size={16} />{' '}{tr('rail.assistant.assistantPanel.insertAsDraft')}
            </button>
          )}
          <span className="spacer" />
          {hasText && turn.model && <ModelChip m={turn.model} />}
          {hasText && (
            <span className="msg-actions">
              <button type="button" className="icon-btn xs" aria-label={tr('rail.assistant.assistantPanel.copyReply')} data-tip={tr('common.copy')} onClick={() => void copyToClipboard(copyText(answer, turn.citations))}>
                <Copy size={14} />
              </button>
              {/* ACT note.from_answer @web.panel: a text note with the answer and its sources, linked to what it cites. */}
              {!turn.error && (
                <button type="button" className="icon-btn xs" aria-label={tr('rail.assistant.assistantPanel.saveToNote')} data-tip={tr('rail.assistant.assistantPanel.saveToNote')} data-testid="answer-to-note"
                  onClick={() => void saveAnswerToNote(qc, answer, turn.citations)}>
                  <StickyNote size={14} />
                </button>
              )}
              {turn.userMessageId != null && !canRetry && (
                <button type="button" className="icon-btn xs" aria-label={tr('rail.assistant.assistantPanel.rerunReply')} data-tip={tr('rail.assistant.assistantPanel.rerunShiftR')} onClick={() => onRerun(turn.userMessageId!)}>
                  <RefreshCw size={14} />
                </button>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/** Where the answer was written: "Local · qwen3" or "Hosted · gpt-5", with the endpoint in the tip.
 *  ACT agent.answer_model @web.panel */
export function ModelChip({ m }: { m: AnswerModel }) {
  const where = m.local ? tr('common.local') : tr('common.hosted')
  const tip = tr('rail.assistant.assistantPanel.on', { itemName: m.name || m.endpoint, endpoint: m.endpoint, value: m.local ? tr('rail.assistant.assistantPanel.aLocalModelOnYour') : tr('rail.assistant.assistantPanel.aHostedModelThisChat') })
  return (
    <span className={`chip-model ${m.local ? 'local' : 'hosted'}`} data-testid="answer-model" data-tip={tip} aria-label={tr('rail.assistant.assistantPanel.model', { where, itemName: m.name || m.endpoint })} tabIndex={0}>
      {m.local ? <Lock size={11} aria-hidden /> : <Cloud size={11} aria-hidden />}
      {where} · {m.name || m.endpoint}
    </span>
  )
}

/** "Updated since": the item moved on after this card's version (by anyone). */
function UpdatedSince({ kind, id, version }: { kind: LiveKind; id: string | number | null; version: number | null }) {
  const latest = useLatestVersion(kind, id)
  if (latest == null || version == null || latest <= version) return null
  return <span className="chat-updated-since" data-testid="updated-since">{tr('rail.assistant.assistantPanel.updatedSince')}</span>
}

/** "Open draft": the live draft, not the copy this card was made with; a window or reply that has
 *  it open comes forward instead of a second one (one window per draft). */
function DraftCardButton({ card, n, conversationId }: { card: DraftCard; n: number | null; conversationId: number | null }) {
  const open = async () => {
    const st = useCompose.getState()
    if (card.draftId == null) {
      st.open({ payload: card.payload, conversationId })
      return
    }
    // ACT draft.open @web.panel
    if (st.windows.some((w) => w.draftId === card.draftId) || focusOpen('draft', card.draftId)) {
      st.open({ draftId: card.draftId })
      return
    }
    try {
      const got = await api.get<{ payload: ComposePayload; version?: number | null }>(`/drafts/${card.draftId}`)
      st.open({ draftId: card.draftId, payload: got.payload, version: got.version ?? null, conversationId })
    } catch (e) {
      toastError(e, tr('rail.assistant.assistantPanel.couldNotOpenTheDraft'))
    }
  }
  return (
    <button type="button" className="btn tonal sm" onClick={() => void open()} data-testid="open-draft">
      <FilePen size={16} />{' '}{tr('rail.assistant.assistantPanel.openDraft', { value: n ? ` ${n}` : '' })}
      <UpdatedSince kind="draft" id={card.draftId} version={card.version} />
    </button>
  )
}

/** `found`: the message a memory's "From a chat" link opened the chat at (marked for a moment). */
function UserBubble({ turn, busy, onEdit, found }: { turn: UserTurn; busy: boolean; onEdit: (id: number, text: string) => void; found?: boolean }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(turn.text)
  const commit = () => {
    setEditing(false)
    if (turn.id != null && text.trim()) onEdit(turn.id, text)
  }
  if (editing && turn.id != null) {
    return (
      <form
        className="chat-edit"
        onSubmit={(e) => {
          e.preventDefault()
          commit()
        }}
      >
        <textarea
          autoFocus
          dir="auto"
          value={text}
          rows={Math.min(8, text.split('\n').length + 1)}
          aria-label={tr('rail.assistant.assistantPanel.editYourMessage')}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              commit()
            }
            if (e.key === 'Escape') setEditing(false)
          }}
        />
        <div className="row">
          <span className="muted chat-edit-note">{tr('rail.assistant.assistantPanel.replacesThisMessageAndThe')}</span>
          <span className="spacer" />
          <button type="button" className="btn text sm" onClick={() => setEditing(false)}>
            {tr('common.cancel')}
          </button>
          <button type="submit" className="btn filled sm" disabled={!text.trim()}>
            {tr('common.send')}
          </button>
        </div>
      </form>
    )
  }
  return (
    <div className={`chat-user${found ? ' chat-found' : ''}`} data-message-id={turn.id ?? undefined}>
      <span className="msg-actions">
        <button type="button" className="icon-btn xs" aria-label={tr('rail.assistant.assistantPanel.copyMessage')} data-tip={tr('common.copy')} onClick={() => void copyToClipboard(turn.text)}>
          <Copy size={14} />
        </button>
        {turn.id != null && !busy && (
          <button
            type="button"
            className="icon-btn xs"
            aria-label={tr('rail.assistant.assistantPanel.editAndResend')}
            data-tip={tr('rail.assistant.assistantPanel.editResend')}
            onClick={() => {
              setText(turn.text)
              setEditing(true)
            }}
          >
            <PenLine size={14} />
          </button>
        )}
      </span>
      <div className="chat-msg user bidi" dir="auto">{turn.text}</div>
      <span className="chat-user-pic">
        <MyAvatar onlyPicture />
      </span>
    </div>
  )
}

/** The assistant chat, or the access policy's message when the assistant is off for this user
 *  (GET /me "access", backend core/access.py). */
export function AssistantPanel({ conversationId }: { conversationId: number | null }) {
  const message = useAssistantOff()
  if (message) {
    return (
      <div className="chat">
        <div className="chat-log">
          <div className="banner" role="status" data-testid="no-assistant" style={{ marginTop: 8 }}>
            {message}
          </div>
        </div>
      </div>
    )
  }
  return <AssistantChat conversationId={conversationId} />
}

/** The no-assistant message, or null while the assistant is on (and on servers without access policies). */
function useAssistantOff(): string | null {
  const assistantName = useAssistantName()
  const access = useMe().data?.access
  if (!access || access.features.assistant) return null
  return access.message ?? tr('rail.assistant.assistantPanel.yourAdministratorHasntGivenYou', { name: assistantName })
}

function AssistantChat({ conversationId }: { conversationId: number | null }) {
  const { turns, busy, sessionId, title, setTitle, skills, send, resume, stop, reset, open, openSession, updateApproval, addReviewCard, rerun, rerunLast, editResend, undoConfig, localOnly, setLocalOnly, notice, retitle } = useAssistant(conversationId)
  const qc = useQueryClient()
  const commandList = useCommands().data?.items
  const skillList = useSkills().data?.items
  const renameChat = useRenameChat(useCallback((id: string, text: string, renamed: boolean) => {
    if (id === sessionId) setTitle({ text, renamed })
  }, [sessionId, setTitle]))
  // `/chats words`: the list opens searched.
  const [listQuery, setListQuery] = useState('')
  // A rename here or in Chats: the header follows when it is the open chat.
  const onRenamed = useCallback((id: string, text: string, renamed: boolean) => {
    if (id === sessionId) setTitle({ text, renamed })
  }, [sessionId, setTitle])
  const [input, setInput] = useState('')
  // Chats (ChatList.tsx): the list of earlier chats takes the panel; the chat stays mounted behind it.
  const [listOpen, setListOpen] = useState(false)
  const closeList = useCallback(() => setListOpen(false), [])
  // Opened by the user (the button, Ctrl + ., a link): the message box takes the focus, so typed
  // words never run mail shortcuts. A panel restored at page load leaves the focus where it is.
  const agentFocusSeq = useUI((s) => s.agentFocusSeq)
  useEffect(() => {
    if (agentFocusSeq > 0) inputRef.current?.focus()
  }, [agentFocusSeq])
  // Back from the list: the focus goes to the message box.
  const wasOpen = useRef(false)
  useEffect(() => {
    if (wasOpen.current && !listOpen) inputRef.current?.focus()
    wasOpen.current = listOpen
  }, [listOpen])
  const openChat = useCallback(
    async (id: string) => {
      const ok = await open(id)
      if (ok) {
        useAskAbout.getState().clear()
        setLocalOnly(false)
        closeList()
      }
      return ok
    },
    [open, closeList, setLocalOnly],
  )
  const newChat = useCallback(() => {
    useAskAbout.getState().clear()
    reset()
    closeList()
  }, [reset, closeList])
  // A deleted chat that was open makes way for a new one.
  const onDeleted = useCallback((id: string) => {
    if (id === sessionId) reset()
  }, [sessionId, reset])
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const conv = useConversation(conversationId).data?.conversation
  const openDrafts = useCompose((s) => new Set(Object.values(s.live).map((d) => d.draftId ?? d.key)).size)
  const waitForTyping = useLive((s) => s.waitForTyping)
  const nav = useAmbroseNav()
  const picker = useSkillPicker(input, setInput, inputRef, true)
  // "Ask assistant" (the ask capture, features/ask): a new chat with the record attached, and the
  // capture's words sent there (Send) or put in the message box (Open chat). The store keeps which
  // ask was handled, so a panel that opens because of the ask still acts on it.
  const about = useAskAbout((s) => s.about)
  const askStamp = useAskAbout((s) => s.stamp)
  useEffect(() => {
    const st = useAskAbout.getState()
    if (askStamp === st.handled) return
    const pending = st.pending
    useAskAbout.setState({ handled: askStamp, pending: null })
    if (busy) {
      // A reply is still running: the words wait in the message box.
      if (pending) {
        setInput(pending.text)
        if (pending.send) toast({ message: tr('rail.assistant.assistantPanel.isStillAnsweringYourWords', { name: assistantNameNow() }) })
      }
    } else {
      reset()
      // After this commit: a panel that just mounted (and React's strict double effects) must not
      // abort the request it starts.
      if (pending?.send) setTimeout(() => send(pending.text), 0)
      else if (pending) setInput(pending.text)
    }
    setListOpen(false)
    inputRef.current?.focus()
  }, [askStamp, busy, reset, send])

  // Follow the output while the reader is at the bottom; scrolling up detaches (useFollowScroll).
  const follow = useFollowScroll(logRef, turns)
  const userTurns = turns.filter((t) => t.role === 'user').length
  const { jump, detach } = follow
  useEffect(() => {
    if (userTurns) jump() // a new message of the user's: back to the latest
  }, [userTurns, jump])

  // A memory's "From a chat" link (useAskAbout.openChat): the chat it came from, with the user's
  // message brought into view and marked for a moment. While a reply runs, the chat stays.
  // The store keeps what was handled and the marked message: the panel can mount again meanwhile.
  const show = useAskAbout((s) => s.show)
  const found = useAskAbout((s) => s.found)
  useEffect(() => {
    if (!show || show.stamp <= useAskAbout.getState().shown) return
    useAskAbout.setState({ shown: show.stamp })
    if (busy) {
      toast({ message: tr('rail.assistant.assistantPanel.stillAnsweringOpenLater', { name: assistantNameNow() }) })
      return
    }
    void openSession(show.session).then((ok) => {
      if (!ok) toast({ message: tr('rail.assistant.assistantPanel.couldNotOpenChat') })
      else useAskAbout.setState({ found: show.message })
    })
  }, [show, busy, openSession])
  const scrolledTo = useRef<number | null>(null)
  useEffect(() => {
    if (found == null || scrolledTo.current === found) return
    const el = logRef.current?.querySelector<HTMLElement>(`[data-message-id="${found}"]`)
    if (!el) return
    scrolledTo.current = found
    detach()
    el.scrollIntoView({ block: 'center' })
  }, [found, turns, detach])

  // Shift+R (a global shortcut) reruns the last reply.
  useEffect(() => {
    const on = () => {
      if (!busy) rerunLast()
    }
    window.addEventListener('mailr:assistant-rerun', on)
    return () => window.removeEventListener('mailr:assistant-rerun', on)
  }, [busy, rerunLast])

  const loc = useLocation()
  const openCitation = useCallback(
    (c: Citation) => {
      if (c.kind === 'recording' && c.connector_id && c.recording_id) {
        useRecordingSheet.getState().show(c.connector_id, c.recording_id, c.t ?? null) // ACT recording.view @web.panel
        return
      }
      if (openWebCitation(c)) return
      const route = citationRoute(c, viewOfLocation(loc.pathname, loc.search))
      if (route) nav(route)
    },
    [nav, loc.pathname, loc.search],
  )

  /** The last answer with text, for /copy and /note; the app's own command replies do not count. */
  const lastAnswer = () => {
    for (let i = turns.length - 1; i >= 0; i--) {
      const t = turns[i]
      if (t.role === 'assistant' && !t.notice && !t.error && turnText(t).trim()) return t
    }
    return null
  }

  /** A slash command the app runs itself (agent/commands.py kind "app"). True when it ran here. */
  const runCommand = (cmd: ChatCommand, text: string, typed: string, list: ChatCommand[]): boolean => {
    if (cmd.kind !== 'app') return false
    const say = (msg: string) => notice(typed, msg)
    const needsChat = () => say(tr('rail.assistant.commands.needsChat', { command: `/${cmd.name}` }))
    switch (cmd.name) {
      case 'new':
        newChat()
        return true
      case 'chats':
        setListQuery(text)
        setListOpen(true)
        return true
      case 'help':
        say(helpText(list, skillList ?? []))
        return true
      case 'local':
        if (localOnly) say(tr('rail.assistant.commands.localAlready'))
        else {
          setLocalOnly(true)
          say(tr('rail.assistant.commands.localOn', { name: assistantNameNow() }))
        }
        return true
      case 'retry':
        if (!turns.some((t) => t.role === 'assistant')) say(tr('rail.assistant.commands.noAnswer'))
        else rerunLast()
        return true
      case 'copy':
      case 'note': {
        const t = lastAnswer()
        if (!t || t.role !== 'assistant') {
          say(tr('rail.assistant.commands.noAnswer'))
          return true
        }
        if (cmd.name === 'copy') void copyToClipboard(copyText(turnText(t), t.citations))
        else void saveAnswerToNote(qc, turnText(t), t.citations)
        return true
      }
      case 'rename':
        if (!sessionId || !title) needsChat()
        else if (!text) say(tr('rail.assistant.commands.needsText', { command: '/rename', example: cmd.example ?? '/rename' }))
        else renameChat({ id: sessionId, title: title.text, renamed: title.renamed }, text)
        return true
      case 'retitle':
        if (!sessionId) needsChat()
        else void retitle().catch(() => toast({ message: tr('rail.assistant.commands.noTitle'), error: true }))
        return true
      case 'delete': {
        if (!sessionId) {
          needsChat()
          return true
        }
        // ACT agent.chat_delete @web.panel: the chat goes now; the DELETE is sent when the toast closes.
        const id = sessionId
        let undone = false
        reset()
        toast({
          message: tr('rail.assistant.chatList.deleted'),
          actionLabel: tr('common.undo'),
          onAction: () => {
            undone = true
            void openSession(id)
          },
          onGone: () => {
            if (undone) return
            void api.del(`/agent/sessions/${encodeURIComponent(id)}`)
              .then(() => qc.setQueriesData<ChatSummary[]>({ queryKey: CHATS_KEY }, (list) => list?.filter((c) => c.id !== id)))
              .catch(() => toast({ message: tr('rail.assistant.chatList.deleteFailed'), error: true }))
          },
        })
        return true
      }
      default:
        return false
    }
  }

  const submitting = useRef(false)
  const submit = async () => {
    const typed = input
    if (!typed.trim() || busy || submitting.current) return
    submitting.current = true
    let list: ChatCommand[] | undefined
    try {
      list = await commandListFor(qc, typed, commandList)
    } finally {
      submitting.current = false
    }
    const command = parseCommand(typed, list)
    if (command && list && runCommand(command.cmd, command.text, typed.trim(), list)) {
      setInput('')
      if (inputRef.current) inputRef.current.style.height = 'auto'
      return
    }
    follow.jump()
    send(typed)
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
  }

  const suggestions = about ? askSuggestions(about) : conversationId ? SUGGESTIONS_CONV : SUGGESTIONS_GENERAL
  const last = turns[turns.length - 1]

  return (
    <>
    {listOpen && <ChatList current={sessionId} onOpen={openChat} onClose={closeList} onDeleted={onDeleted} onRenamed={onRenamed} initialQuery={listQuery} />}
    {!listOpen && sessionId && title?.text.trim() && (
      <HeaderTitle>
        <ChatHeaderTitle id={sessionId} title={title} onRenamed={onRenamed} />
      </HeaderTitle>
    )}
    <div className="chat" style={listOpen ? { display: 'none' } : undefined}>
      <div className="chat-log" ref={logRef} aria-live="polite" aria-busy={busy}>
        {turns.length === 0 && (
          <div>
            <p className="muted" style={{ fontSize: 13, margin: '4px 0 0' }}>
              {tx('rail.assistant.assistantPanel.searchesYourMailReadsConversations', { name: assistantNameNow() }, { code: (c) => <code>{c}</code> })}
            </p>
            <div className="chat-suggestions" style={{ padding: '12px 0' }}>
              {suggestions.map((s) => (
                <button key={s} type="button" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t, i) =>
          t.role === 'user' ? (
            <UserBubble key={`u${i}-${t.id ?? ''}`} turn={t} busy={busy} onEdit={editResend} found={found != null && t.id === found} />
          ) : (
            <TurnView
              key={`a${i}`}
              turn={t}
              index={i}
              isLast={i === turns.length - 1}
              conversationId={conversationId}
              conv={conv}
              onOpen={openCitation}
              onApproval={updateApproval}
              onReviewCard={addReviewCard}
              onResume={resume}
              onRerun={rerun}
              onUndo={undoConfig}
            />
          ),
        )}
      </div>
      {/* Detached from newer output: also once the turn ends or waits on an approval card. */}
      {follow.showJump && (
        <button type="button" className="chat-jump" onClick={follow.jump} data-testid="chat-jump">
          <ArrowDown size={14} aria-hidden />{' '}{tr('rail.assistant.assistantPanel.jumpToLatest')}
        </button>
      )}
      {(conv || about || skills.length > 0 || openDrafts > 0 || localOnly) && (
        <div className="chat-context">
          {localOnly && (
            <span className="chat-ctx-chip" data-testid="chat-local-only" data-tip={tr('rail.assistant.commands.localTip', { name: assistantNameNow() })}>
              <Lock size={12} aria-hidden />{' '}{tr('rail.assistant.commands.localChip')}
              <button type="button" className="chat-ctx-x" aria-label={tr('rail.assistant.commands.localOff')} onClick={() => setLocalOnly(false)}><X size={12} /></button>
            </span>
          )}
          {about && !(about.kind === 'conversation' && about.id === conversationId) && (
            <span className="chat-ctx-chip chat-ctx-about" data-testid="chat-about" data-tip={tr('rail.assistant.assistantPanel.readsThisWithEveryMessage', { name: assistantNameNow() })}>
              {about.kind === 'event' ? <CalendarDays size={12} aria-hidden /> : about.kind === 'recording' ? <Play size={12} aria-hidden /> : about.kind === 'conversation' ? <Mail size={12} aria-hidden /> : about.kind === 'approval' ? <ShieldCheck size={12} aria-hidden /> : about.kind === 'note' ? <StickyNote size={12} aria-hidden /> : <ListChecks size={12} aria-hidden />}
              <span className="ellipsis">{askLabel(about)}</span>
              <button type="button" className="chat-ctx-x" aria-label={tr('rail.assistant.assistantPanel.detach')} onClick={() => useAskAbout.getState().clear()}><X size={12} /></button>
            </span>
          )}
          {conv && (
            <span className="chat-ctx-conv">
              <Mail size={14} aria-hidden /> <span className="ellipsis" dir="auto">{conv.subject || tr('common.noSubject')}</span>
            </span>
          )}
          {openDrafts > 0 && (
            <span className="chat-ctx-chip" data-tip={tr('rail.assistant.assistantPanel.canSeeAndEditYour', { name: assistantNameNow() })}>
              <FilePen size={12} aria-hidden /> {tr('rail.assistant.assistantPanel.openDrafts', { count: openDrafts })}
            </span>
          )}
          {openDrafts > 0 && (
            // ACT agent.wait_for_typing @web.panel: the assistant's changes to an open draft wait until you pause.
            // A switch with its state in sight: a chip that read like a message was not seen as a setting.
            <button type="button" role="switch" className={`chat-ctx-chip chat-ctx-toggle${waitForTyping ? ' on' : ''}`} aria-checked={waitForTyping}
              data-tip={tr('rail.assistant.assistantPanel.waitsUntilYouPauseTyping', { name: assistantNameNow() })} data-testid="wait-for-typing"
              onClick={() => useLive.setState({ waitForTyping: !waitForTyping })}>
              <Keyboard size={12} aria-hidden />{' '}{tr('rail.assistant.assistantPanel.letMeFinishTypingFirst')}
              <span className="chat-ctx-state" aria-hidden>{waitForTyping ? tr('rail.assistant.assistantPanel.switchOn') : tr('common.off')}</span>
            </button>
          )}
          {skills.map((s) => (
            <span key={s} className="chat-ctx-chip" data-tip={tr('rail.assistant.assistantPanel.skillInUseInThis')}>
              <Sparkles size={12} aria-hidden />
              {`/${s}`}
            </span>
          ))}
        </div>
      )}
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {picker.popover}
        <textarea
          ref={inputRef}
          dir="auto"
          rows={1}
          value={input}
          placeholder={last?.role === 'assistant' && last.approvals.some((a) => a.status === 'pending') ? tr('rail.assistant.assistantPanel.waitingForYourApprovalAbove') : tr('rail.assistant.assistantPanel.askAboutYourMailFor', { name: assistantNameNow() })}
          aria-label={tr('rail.assistant.assistantPanel.message', { name: assistantNameNow() })}
          aria-autocomplete="list"
          aria-expanded={picker.open}
          onChange={(e) => {
            setInput(e.target.value)
            picker.sync()
            e.target.style.height = 'auto'
            e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`
          }}
          onSelect={picker.sync}
          onKeyDown={(e) => {
            if (picker.onKeyDown(e)) return
            // Ctrl + . closes the panel from the message box too (the shortcut that opened it).
            if ((e.ctrlKey || e.metaKey) && e.key === '.') {
              e.preventDefault()
              useUI.getState().setPanel(null)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              void submit()
            }
          }}
        />
        {/* ACT agent.dictate @web.panel: the words land in the box, to read before sending. */}
        <MicButton disabled={busy} context={dictationContext(sessionId, about, conversationId !== null || localOnly)} onText={(t) => {
          setInput((v) => appendWords(v, t))
          requestAnimationFrame(() => {
            const el = inputRef.current
            if (!el) return
            el.focus()
            el.style.height = 'auto'
            el.style.height = `${Math.min(160, el.scrollHeight)}px`
          })
        }} />
        {busy ? (
          <button type="button" className="chat-send stop" aria-label={tr('common.stop')} data-tip={tr('common.stop')} onClick={stop}>
            <Square />
          </button>
        ) : (
          <button type="submit" className="chat-send" aria-label={tr('common.send')} data-tip={tr('rail.assistant.assistantPanel.sendEnter')} disabled={!input.trim()}>
            <ArrowUp />
          </button>
        )}
      </form>
      <HeaderAction>
        {/* ACT agent.chat_list @web.panel: earlier chats, from the panel's header (no new navigation entry). */}
        <button type="button" className={`icon-btn sm${listOpen ? ' active' : ''}`} aria-label={tr('rail.assistant.chatList.chats')} aria-pressed={listOpen}
          data-tip={listOpen ? tr('rail.assistant.chatList.backToChat') : tr('rail.assistant.chatList.chats')} data-testid="chats-toggle"
          onClick={() => (listOpen ? closeList() : setListOpen(true))}>
          <History />
        </button>
        <button type="button" className="icon-btn sm" aria-label={tr('rail.assistant.assistantPanel.newChat')} data-tip={tr('rail.assistant.assistantPanel.newChat')} disabled={!listOpen && ((turns.length === 0 && !about) || busy)} onClick={newChat}>
          <SquarePen />
        </button>
      </HeaderAction>
    </div>
    </>
  )
}

/** `/help`: the commands by group, then the user's skills (Markdown, shown as an answer). */
function helpText(list: ChatCommand[], skills: { name: string; title: string; description: string; enabled: boolean; auto: boolean }[]): string {
  const groups: [ChatCommand['group'], string][] = [
    ['chat', tr('rail.assistant.commands.groupChat')], ['add', tr('rail.assistant.commands.groupAdd')], ['ask', tr('rail.assistant.commands.groupAsk')],
  ]
  const parts = groups.map(([g, head]) => {
    const rows = list.filter((c) => c.group === g)
      .map((c) => `- \`/${c.name}\`${c.aliases.length ? ` (${c.aliases.map((a) => `\`/${a}\``).join(', ')})` : ''} — ${c.summary}`)
    return `**${head}**\n${rows.join('\n')}`
  })
  const mine = skills.filter((s) => s.enabled && !s.auto)
  if (mine.length) parts.push(`**${tr('common.skills')}**\n${mine.map((s) => `- \`/${s.name}\` — ${s.description || s.title}`).join('\n')}`)
  parts.push(tr('rail.assistant.commands.helpHint'))
  return parts.join('\n\n')
}

/** Renders into the side panel's header, after its name (SideRail.tsx `side-panel-title`). */
function HeaderTitle({ children }: { children: ReactNode }) {
  const [el, setEl] = useState<HTMLElement | null>(null)
  useEffect(() => setEl(document.getElementById('side-panel-title')), [])
  return el ? createPortal(children, el) : null
}

/**
 * The open chat's title in the panel's header. ACT agent.chat_rename @web.panel: a double-click (or
 * Enter on the focused title) turns it into a text field; Enter saves, Esc cancels, leaving it saves.
 * The rename has Undo in its toast (useRenameChat).
 */
function ChatHeaderTitle({ id, title, onRenamed }: { id: string; title: ChatTitle; onRenamed: (id: string, text: string, renamed: boolean) => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(title.text)
  const rename = useRenameChat(onRenamed)
  const done = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!editing) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [editing])
  const start = () => {
    done.current = false
    setText(title.text)
    setEditing(true)
  }
  const finish = (value: string | null) => {
    if (done.current) return
    done.current = true
    setEditing(false)
    if (value != null) rename({ id, title: title.text, renamed: title.renamed }, value)
    requestAnimationFrame(() => buttonRef.current?.focus())
  }
  if (editing) {
    return (
      <input ref={inputRef} className="chat-head-title-input" value={text} maxLength={TITLE_MAX} dir="auto" data-own-esc=""
        aria-label={tr('rail.assistant.chatList.renameLabel')} title={tr('rail.assistant.chatList.renameHint')} data-testid="chat-title-input"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => finish(text)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
            e.preventDefault()
            finish(text)
          } else if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            finish(null)
          }
        }} />
    )
  }
  return (
    <button ref={buttonRef} type="button" className="chat-head-title ellipsis" dir="auto" data-testid="chat-title"
      aria-label={tr('rail.assistant.chatList.renameTitled', { title: title.text })} data-tip={tr('rail.assistant.chatList.renameTip')}
      aria-keyshortcuts="F2"
      // A mouse click does nothing; a double-click renames. Enter or Space (detail 0) and F2 rename too.
      onClick={(e) => {
        if (e.detail === 0) start()
      }}
      onDoubleClick={start}
      onKeyDown={(e) => {
        if (e.key === 'F2') {
          e.preventDefault()
          start()
        }
      }}>
      {title.text}
    </button>
  )
}

/** Renders into the side panel's header (next to Close). */
function HeaderAction({ children }: { children: ReactNode }) {
  const [el, setEl] = useState<HTMLElement | null>(null)
  useEffect(() => setEl(document.getElementById('side-panel-actions')), [])
  return el ? createPortal(children, el) : null
}

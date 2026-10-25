import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { flushSync } from 'react-dom'
import { useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import type { Editor as TiptapEditor } from '@tiptap/react'
import { ApiError, api } from '../../api/client'
import { qk, useActions, useMe, useSettings } from '../../api/hooks'
import type { Address, ComposePayload, Message, Outbox } from '../../api/types'
import { isEmptyHtml, joinQuote, mentionsAttachment, splitQuote } from '../../lib/html'
import { isValidEmail } from '../../lib/format'
import { formatOptionTime } from '../../lib/snooze'
import { mailPath, viewAccount } from '../../lib/views'
import { useMailAccounts } from '../layout/accounts'
import { useCompose, type DraftEdit } from '../../stores/compose'
import { hasLocalDraft, removeLocalDraft } from '../../lib/draftStore'
import { registerEditor } from '../../lib/live/editors'
import { LiveDoc, changedUnits, type ConflictState } from '../../lib/live/machine'
import { same } from '../../lib/live/merge3'
import { byName, noteVersion } from '../../lib/live/store'
import { fetchLive, reportTyping, useLiveItem } from '../../lib/live/useLiveItem'
import { toast, toastError } from '../../stores/ui'
import { ConflictBar, EditingBanner } from '../live/LiveBars'
import { plainSendError, unwatchSend, useOutboxActions, watchSend } from '../mail/outbox'
import { useSkillPicker } from '../rail/assistant/SkillPicker'
import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { AssistNoteBar, RecipientProposalBar } from './AssistBars'
import { AttachmentList } from './AttachmentList'
import { ComposeFooter, type HeldSend } from './ComposeFooter'
import { ComposeHeader } from './ComposeHeader'
import { DraftGoneNotice, DraftLoadingNotice, DraftRestoredNotice, NoIdentityNotice } from './ComposeNotices'
import { attKey, heldAttachment, type AssistNote, type RecipientProposal, type Snapshot } from './composeModel'
import { applyEditsToHtml, asEditorHtml, editBodyHtml, textToParagraphs } from './composeText'
import { EditorArea, FormatBar, useMailEditor } from './Editor'
import { HelpMeWriteBar } from './HelpMeWriteBar'
import { ImageToolbar } from './ImageToolbar'
import { sendAttempt } from './sendAttempt'
import { useAttachments } from './useAttachments'
import { useComposeTemplates } from './useComposeTemplates'
import { draftJson, useDraftLocalCopy } from './useDraftLocalCopy'
import { useInlineImages } from './useInlineImages'
import { t as tr } from '../../i18n'

// The compose form is split into parts: the send bar (ComposeFooter), the
// header (ComposeHeader), the notices, Help me write, the assistant's notes, the attachment list,
// and the hooks for attachments, templates and the local copy. The form keeps the draft's state, saving, live editing and sending.
export { applyEditsToHtml, textToParagraphs } from './composeText'

export interface ComposeFormProps {
  initial: ComposePayload
  draftId: number | null
  conversationId?: number | null
  variant: 'window' | 'inline'
  onDraftId?: (id: number) => void
  onSubjectChange?: (s: string) => void
  onClose: (reason: 'sent' | 'discarded' | 'closed') => void
  /** `localId`: the inline form's local copy, which the window takes over. */
  onPopOut?: (payload: ComposePayload, draftId: number | null, localId: string) => void
  assist?: boolean
  autoFocusBody?: boolean
  /** Receives the form's close function (saves the draft), for the window's X button. */
  registerClose?: (fn: () => Promise<void>) => void
  /** Key under which the form reports what it shows to the assistant (compose window key, or
   *  "inline-<conversation>"), and receives the assistant's edits. */
  liveKey?: string
  /** The id of this form's local copy (lib/draftStore.ts); a new one when absent. */
  localId?: string
  /** The form reopens a local copy: say so, and save it to the server. */
  restored?: boolean
  /** The draft's live version when `initial` was read (lib/live); fetched when absent. */
  initialVersion?: number | null
  /** Bring the form's window or tab forward ("Open draft" for a draft already open). */
  onFocusRequest?: () => void
  /** The user changed something in the form (the inline slot then never replaces it). */
  onTouched?: () => void
}

/** A draft's text fields for the three-way merge (backend core/live.py TEXT_FIELDS). */
const DRAFT_TEXT = { html: 'html', text: 'lines' } as const
type DraftContent = ComposePayload & Record<string, unknown>


export function ComposeForm({ initial, draftId: initialDraftId, conversationId, variant, onDraftId, onSubjectChange, onClose, onPopOut, assist, autoFocusBody, registerClose, liveKey, localId: givenLocalId, restored, initialVersion, onFocusRequest, onTouched }: ComposeFormProps) {
  const me = useMe().data
  const settings = useSettings()
  const identities = useMemo(() => me?.identities ?? [], [me?.identities])
  // A new message written inside an account's own folders starts from that account's address.
  const { byId: accountsById } = useMailAccounts()
  const { pathname } = useLocation()
  const routeAccount = accountsById.get(viewAccount(decodeURIComponent(/^\/mail\/([^/]+)/.exec(pathname)?.[1] ?? '')) ?? -1)
  const defaultIdentity = identities.find((i) => i.id === routeAccount?.identity_id) ?? identities.find((i) => i.is_default) ?? identities[0]
  const qc = useQueryClient()
  const nav = useNavigate()
  const outbox = useOutboxActions()
  const { run } = useActions()
  const openCompose = useCompose((s) => s.open)

  const split = useMemo(() => splitQuote(initial.html), [initial.html])
  const [identityId, setIdentityId] = useState<number | null>(initial.identity_id ?? defaultIdentity?.id ?? null)
  const [to, setTo] = useState<Address[]>(initial.to ?? [])
  // Send's complaint about the recipients, shown under them until they change (a toast far from
  // the form, one per press, piled up).
  const [recipError, setRecipError] = useState<string | null>(null)
  const [cc, setCc] = useState<Address[]>(initial.cc ?? [])
  const [bcc, setBcc] = useState<Address[]>(initial.bcc ?? [])
  const [subject, setSubject] = useState(initial.subject ?? '')
  const [body, setBody] = useState(split.body)
  const [quote, setQuote] = useState(split.quote)
  const [showQuote, setShowQuote] = useState(false)
  const [showCc, setShowCc] = useState((initial.cc ?? []).length > 0)
  const [showBcc, setShowBcc] = useState((initial.bcc ?? []).length > 0)
  const [showFormat, setShowFormat] = useState(false)
  const [includeSig, setIncludeSig] = useState(true)
  const { atts, setAtts, addFiles, uploading } = useAttachments(initial.attachments, () => setDirty(true))
  const [draftId, setDraftId] = useState<number | null>(initialDraftId)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>(initialDraftId ? 'saved' : 'idle')
  const [saveError, setSaveError] = useState('')
  // A restored local copy (or one a popped-out reply carries) is newer than the server's: save it.
  const [carried] = useState(() => !!restored || hasLocalDraft(givenLocalId))
  const [dirty, setDirty] = useState(carried)
  const [restoredNote, setRestoredNote] = useState(!!restored)
  const [sending, setSending] = useState(false)
  // A second Send before the first answered (a double click, Ctrl+Enter twice) does nothing.
  const sendingRef = useRef(false)
  // One Idempotency-Key per send attempt, kept while its outcome is unknown (sendAttempt.ts).
  const attempt = useRef(sendAttempt())
  const [dragOver, setDragOver] = useState(false)
  const [aiOpen, setAiOpen] = useState(!!assist)
  const [aiText, setAiText] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [assistNote, setAssistNote] = useState<AssistNote | null>(null)
  const [proposal, setProposal] = useState<RecipientProposal | null>(null)
  const [flash, setFlash] = useState(false)
  const aiRef = useRef<HTMLTextAreaElement>(null)
  const picker = useSkillPicker(aiText, setAiText, aiRef)
  const [confirmEmpty, setConfirmEmpty] = useState<HeldSend>(null)
  const [confirmAttach, setConfirmAttach] = useState<HeldSend>(null)
  const saveChain = useRef<Promise<unknown>>(Promise.resolve())
  const draftIdRef = useRef(draftId)
  useEffect(() => {
    draftIdRef.current = draftId
  }, [draftId])

  // Identities load after /me; pick the default once available.
  if (identityId === null && defaultIdentity) setIdentityId(defaultIdentity.id)
  const identity = identities.find((i) => i.id === identityId) ?? defaultIdentity

  const onTouchedRef = useRef(onTouched)
  onTouchedRef.current = onTouched
  const touch = () => {
    lastTouched.current = Date.now()
    setDirty(true)
    // "Draft saved" is about the text before this change.
    setSaveState((st) => (st === 'saved' ? 'idle' : st))
    onTouchedRef.current?.()
  }

  const sendRef = useRef<() => void>(() => {})
  const addFilesRef = useRef<(files: File[]) => void>(() => {})
  const insertImagesRef = useRef<(files: File[], pos?: number) => void>(() => {})
  const templateKeyRef = useRef<(ed: TiptapEditor, e: KeyboardEvent) => boolean>(() => false)
  const editor = useMailEditor({
    html: split.body,
    onChange: (h) => {
      setBody(h)
      touch()
      reportTyping('draft', draftIdRef.current)
    },
    placeholder: variant === 'inline' ? '' : '',
    autoFocus: !!autoFocusBody,
    onSubmit: () => sendRef.current(),
    onPasteFiles: (f) => addFilesRef.current(f),
    onInsertImages: (f, pos) => insertImagesRef.current(f, pos),
    onKeyDown: (ed, e) => templateKeyRef.current(ed, e),
  })

  // ACT mail.insert_image @web.editor: images in the body (upload, placeholder, resize, attachment).
  const images = useInlineImages(editor, {
    onAttach: (a) => {
      setAtts((l) => [...l, { ...a, key: attKey(), progress: 1 }])
      setDirty(true)
    },
  })
  useEffect(() => {
    insertImagesRef.current = (f, pos) => void images.insert(f, pos)
  }, [images])
  const replaceRef = useRef<HTMLInputElement>(null)
  const replacePos = useRef<number | null>(null)
  const { templates, insertTemplate } = useComposeTemplates({
    editor, to, cc, bcc, identityId: identity?.id ?? null, subject, setSubject, touch, keyRef: templateKeyRef,
  })

  const payload = useCallback(
    (withSignature: boolean): ComposePayload => ({
      identity_id: identity?.id ?? null,
      to,
      cc,
      bcc,
      subject,
      // A placeholder still uploading (blob: source) is left out.
      html: joinQuote(body.replace(/<img\b[^>]*\ssrc="blob:[^"]*"[^>]*>/g, ''), showQuote ? '' : quote, withSignature && includeSig && identity?.signature_html ? identity.signature_html : undefined),
      in_reply_to_message_id: initial.in_reply_to_message_id ?? null,
      forward_of_message_id: initial.forward_of_message_id ?? null,
      attachments: atts.filter((a) => a.blob).map((a) => ({ blob: a.blob!, filename: a.filename, type: a.type, size: a.size })),
      headers: initial.headers,
    }),
    [identity, to, cc, bcc, subject, body, quote, showQuote, includeSig, atts, initial],
  )

  const hasContent = !isEmptyHtml(body) || subject.trim() !== '' || to.length + cc.length + bcc.length > 0 || atts.length > 0

  // ----- the local copy -----
  const { localId, syncedRef, touchedRef, serverJson, latest, doneRef, pinnedRef, unsavedNow, writeLocal, finished } = useDraftLocalCopy({
    givenLocalId, carried, owner: me?.user?.id ?? null, conversationId, draftIdRef, payload, hasContent, dirty,
  })

  // ----- live editing (lib/live) -----
  // The version this form started from and its content: saves send it as base_version, so the
  // server merges what others saved meanwhile; others' saves arrive as live events.
  const docRef = useRef<LiveDoc<DraftContent> | null>(null)
  if (docRef.current === null) {
    docRef.current = new LiveDoc<DraftContent>(DRAFT_TEXT, initialDraftId && initialVersion != null ? { version: initialVersion, content: initial as DraftContent } : null)
  }
  const doc = docRef.current
  const [loadingBase, setLoadingBase] = useState(() => !carried && !doc.base && !!initialDraftId)
  useEffect(() => {
    // No change event: making the editor read-only is not an edit.
    editor?.setEditable(!loadingBase, false)
  }, [editor, loadingBase])
  const [conflict, setConflict] = useState<ConflictState<DraftContent> | null>(null)
  const conflictRef = useRef(conflict)
  conflictRef.current = conflict
  const [goneNote, setGoneNote] = useState(false)
  const dirtyRef = useRef(dirty)
  dirtyRef.current = dirty
  const lastTouched = useRef(Date.now())
  const formRef = useRef<HTMLDivElement>(null)
  const payloadRef = useRef(payload)
  payloadRef.current = payload
  const hasContentRef = useRef(hasContent)
  hasContentRef.current = hasContent

  /** Show a stored payload in the form (a remote change, Undo, a merge, "Use theirs"). The
   *  paragraphs that changed light up for a moment. */
  const applyContent = useCallback(
    (p: ComposePayload, opts: { saved: boolean; highlight?: boolean }) => {
      if (!editor) return
      const before = Array.from(editor.view.dom.children, (c) => c.outerHTML)
      const split = splitQuote(p.html ?? '')
      const html = showQuote ? `${split.body}${split.quote ? `<br>${split.quote}` : ''}` : split.body
      editor.commands.setContent(html ? asEditorHtml(html) : p.text ? textToParagraphs(p.text) : '', { emitUpdate: false })
      // Rendered at once: a save that runs next (queued behind the one whose answer this is) reads
      // what the form shows through payloadRef, which must already hold this content.
      flushSync(() => {
        setBody(editor.getHTML())
        if (!showQuote) setQuote(split.quote)
        setSubject(p.subject ?? '')
        setTo(p.to ?? [])
        setCc(p.cc ?? [])
        setBcc(p.bcc ?? [])
        if ((p.cc ?? []).length) setShowCc(true)
        if ((p.bcc ?? []).length) setShowBcc(true)
        if (p.identity_id) setIdentityId(p.identity_id)
        if (p.attachments) setAtts(p.attachments.map(heldAttachment))
        if (opts.saved) {
          syncedRef.current = true
          setDirty(false)
        } else {
          touchedRef.current = true
          setDirty(true)
        }
      })
      if (opts.highlight !== false) {
        const after = Array.from(editor.view.dom.children)
        const changed = changedUnits(before, after.map((c) => c.outerHTML))
        for (const i of changed) after[i]?.classList.add('live-changed')
        setTimeout(() => after.forEach((c) => c.classList.remove('live-changed')), 2400)
        if (!changed.length) flashEditor()
      }
    },
    [editor, showQuote, setAtts, syncedRef, touchedRef],
  )

  const onRemote = useCallback(
    (theirs: { version: number | null; content: DraftContent; by?: string | null }) => {
      const mine = payloadRef.current(false) as DraftContent
      const d = doc.onRemote(theirs, mine, dirtyRef.current || unsavedNow())
      const who = byName(theirs.by)
      const Who = who.charAt(0).toUpperCase() + who.slice(1)
      if (d.type === 'replace') {
        applyContent(d.content, { saved: true })
        // ACT draft.live_update @web.editor @web.reader: Undo puts the text back as a new version.
        toast({
          message: tr('compose.composeForm.updatedBy', { who }), actionLabel: tr('common.undo'), duration: 10_000,
          onAction: () => applyContent(d.before, { saved: false }),
        })
      } else if (d.type === 'merge') {
        applyContent(d.content, { saved: false })
        toast({ message: tr('compose.composeForm.changedThisDraftTooBoth', { Who }), actionLabel: tr('common.undo'), duration: 10_000,
          onAction: () => applyContent(d.before, { saved: false }) })
      } else if (d.type === 'conflict') {
        setConflict(d.conflict)
      }
    },
    [doc, applyContent, unsavedNow],
  )
  // The load below outlives the render that started it: it calls the current onRemote (the editor exists by then).
  const onRemoteRef = useRef(onRemote)
  onRemoteRef.current = onRemote
  const editing = useLiveItem<DraftContent>({
    kind: 'draft', id: draftId, onRemote, settled: () => saveChain.current, version: () => doc.version,
    onDeleted: () => {
      if (!doneRef.current) setGoneNote(true)
    },
  })
  // A draft opened without its version: read it, and catch up when it moved on since the payload was read.
  // Until it answers the form is read-only ("Loading the latest copy…"): what the user typed on the
  // older copy could otherwise be saved over newer text (the first save would carry no base_version).
  // A carried local copy stays editable: it is newer than the server's text by definition.
  useEffect(() => {
    if (!initialDraftId || doc.base) return
    let alive = true
    fetchLive<DraftContent>('draft', initialDraftId)
      .finally(() => {
        if (alive) setLoadingBase(false)
      })
      .then((got) => {
        if (!alive || doc.base) return
        // A carried local copy (restored after a reload, or a popped-out reply's) is newer than the
        // server's text: the server's version is its base, and the next save sends the local text on top.
        if (carried || same(got.content, initial)) doc.setBase(got.version, got.content)
        else {
          doc.setBase(null, initial as DraftContent)
          onRemoteRef.current({ version: got.version, content: got.content, by: got.by })
        }
      })
      .catch(() => {})
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialDraftId])

  // ----- what the assistant sees, and edits it makes -----
  const mode: 'new' | 'reply' | 'reply_all' | 'forward' = initial.forward_of_message_id ? 'forward' : initial.in_reply_to_message_id ? ((initial.cc ?? []).length ? 'reply_all' : 'reply') : 'new'
  useEffect(() => {
    if (!liveKey) return
    const t = setTimeout(() => {
      useCompose.getState().setLive({
        key: liveKey, draftId, mode,
        to: to.map((a) => a.email), cc: cc.map((a) => a.email), bcc: bcc.map((a) => a.email), subject,
        body: editor ? editor.getText({ blockSeparator: '\n\n' }) : '',
        inReplyTo: initial.in_reply_to_message_id ?? null, conversationId: conversationId ?? null, updatedAt: Date.now(),
      })
    }, 250)
    return () => clearTimeout(t)
  }, [liveKey, draftId, mode, to, cc, bcc, subject, body, editor, initial.in_reply_to_message_id, conversationId])
  useEffect(() => {
    if (!liveKey) return
    return () => useCompose.getState().removeLive(liveKey)
  }, [liveKey])

  const snapshot = useCallback((): Snapshot => ({ html: editor?.getHTML() ?? body, subject, to, cc, bcc }), [editor, body, subject, to, cc, bcc])
  const restore = (snap: Snapshot) => {
    editor?.commands.setContent(snap.html, { emitUpdate: true })
    setSubject(snap.subject)
    setTo(snap.to)
    setCc(snap.cc)
    setBcc(snap.bcc)
    setAssistNote(null)
    touch()
  }
  const flashEditor = () => {
    setFlash(true)
    setTimeout(() => setFlash(false), 1600)
  }

  const applyEdit = useCallback(
    (edit: DraftEdit) => {
      if (!editor) return
      const undo = snapshot()
      const changes: { old: string; new: string }[] = []
      if (edit.edits?.length) {
        const html = applyEditsToHtml(editor.getHTML(), edit.edits)
        if (html !== null) editor.commands.setContent(html, { emitUpdate: true })
        else if (edit.body_text != null) editor.commands.setContent(editBodyHtml(edit), { emitUpdate: true })
        changes.push(...edit.edits.map((e) => ({ old: e.find, new: e.replace })))
      } else if (edit.body_text != null) {
        editor.commands.setContent(editBodyHtml(edit), { emitUpdate: true })
        changes.push({ old: '', new: tr('compose.composeForm.newText') })
      }
      if (edit.subject != null) {
        changes.push({ old: subject, new: edit.subject })
        setSubject(edit.subject)
      }
      // Recipients are never changed by the assistant directly (older servers sent them as
      // to/cc/bcc): they become a proposal the user accepts or rejects.
      const asked: RecipientProposal = {}
      for (const k of ['to', 'cc', 'bcc'] as const) {
        const list = edit.proposed_recipients?.[k] ?? edit[k]
        if (list) asked[k] = list.map((a) => ({ email: a.email, name: a.name ?? '' }))
      }
      if (Object.keys(asked).length) setProposal(asked)
      if (changes.length) {
        setAssistNote({ title: tr('compose.composeForm.editedThisDraft', { name: assistantNameNow() }), changes, undo })
        flashEditor()
        touch()
      }
    },
    [editor, snapshot, subject],
  )
  const acceptProposal = () => {
    if (!proposal) return
    if (proposal.to) setTo(proposal.to)
    if (proposal.cc) {
      setCc(proposal.cc)
      setShowCc(true)
    }
    if (proposal.bcc) {
      setBcc(proposal.bcc)
      setShowBcc(true)
    }
    setProposal(null)
    touch()
  }
  const pendingEdits = useCompose((s) => (liveKey ? s.edits[liveKey]?.length ?? 0 : 0))
  useEffect(() => {
    if (!liveKey || !pendingEdits || !editor) return
    for (const e of useCompose.getState().takeEdits(liveKey)) applyEdit(e)
  }, [liveKey, pendingEdits, editor, applyEdit])

  /** Requested saves, newest last: an answer for an older one never says more than it knows. */
  const saveSeq = useRef(0)
  /** After save `seq` answered: "Draft saved" only when the form shows what the server holds;
   *  "Saving…" while a newer save waits behind it; nothing while newer typing waits for the pause. */
  const settleSaveState = useCallback((seq: number) => {
    if (draftJson(payloadRef.current(false)) === serverJson.current) setSaveState('saved')
    else setSaveState(seq === saveSeq.current ? 'idle' : 'saving')
  }, [serverJson])

  // ACT draft.save @web.reader
  const saveDraft = useCallback(
    () => {
      const seq = ++saveSeq.current
      const job = saveChain.current.then(async () => {
        // A save queued behind another runs after that one's answer, which may have rebased the form
        // (merged with changes saved meanwhile): it sends what the form shows now, never the older
        // copy it was asked with, which would undo the merge. Nothing new since the last answer: no PUT.
        const p = payloadRef.current(false)
        if (draftIdRef.current && !doc.conflict && draftJson(p) === serverJson.current) {
          settleSaveState(seq)
          return
        }
        setSaveState('saving')
        try {
          const id = draftIdRef.current
          type Saved = { draft: Message; version?: number | null; merged?: boolean; payload?: ComposePayload }
          let res: Saved
          if (id) {
            try {
              res = await api.put<Saved>(`/drafts/${id}`, { payload: p, base_version: doc.version } as unknown as Record<string, unknown>)
            } catch (e) {
              if (e instanceof ApiError && e.status === 404) {
                // Sent or deleted elsewhere: the text is kept as a new draft.
                draftIdRef.current = null
                setDraftId(null)
                doc.base = null
                const made = await api.post<Saved>('/drafts', { payload: p } as unknown as Record<string, unknown>)
                draftIdRef.current = made.draft.id
                setDraftId(made.draft.id)
                onDraftId?.(made.draft.id)
                setGoneNote(false)
                doc.setBase(made.version ?? null, p as DraftContent)
                serverJson.current = draftJson(p)
                settleSaveState(seq)
                return
              }
              if (e instanceof ApiError && e.status === 409 && e.body && typeof e.body === 'object') {
                // ACT draft.resolve_conflict @web.editor @web.reader: both changed the same part; nothing was overwritten.
                setConflict(doc.onRejected(e.body as Parameters<typeof doc.onRejected>[0], payloadRef.current(false) as DraftContent))
                setSaveState('idle')
                return
              }
              throw e
            }
          } else {
            res = await api.post<Saved>('/drafts', { payload: p } as unknown as Record<string, unknown>)
            if (res?.draft?.id) {
              draftIdRef.current = res.draft.id
              setDraftId(res.draft.id)
              onDraftId?.(res.draft.id)
            }
          }
          setSaveError('')
          const did = draftIdRef.current
          if (did) noteVersion('draft', did, res?.version ?? null)
          // The server merged this save with changes saved meanwhile: the form takes the result,
          // keeping what was typed while the save was on its way.
          const fix = doc.onSaved(p as DraftContent, res?.version ?? null, res?.merged && res.payload ? (res.payload as DraftContent) : null, payloadRef.current(false) as DraftContent)
          // The server holds this text now: it is the source of truth, unless newer text is typed.
          serverJson.current = draftJson(res?.merged && res.payload ? res.payload : p)
          if (fix?.conflict) setConflict(doc.conflict)
          else if (fix) {
            const saved = draftJson(fix.content) === draftJson(res.payload ?? {})
            applyContent(fix.content, { saved })
            // The form shows the server's copy as the editor writes it (its HTML may differ in form,
            // such as dir="auto" on a paragraph): that rendering counts as saved.
            if (saved) serverJson.current = draftJson(payloadRef.current(false))
          }
          // Pinned (the form is going away): keep the copy, now pointing at the server draft.
          if (pinnedRef.current) writeLocal()
          else if (latest.current?.json === serverJson.current) removeLocalDraft(localId)
          settleSaveState(seq)
          void qc.invalidateQueries({ queryKey: qk.conversations })
          void qc.invalidateQueries({ queryKey: qk.labels })
        } catch (e) {
          setSaveState('error')
          setSaveError(e instanceof Error ? e.message : '')
          throw e
        }
      })
      saveChain.current = job.catch(() => {})
      return job
    },
    [onDraftId, qc, localId, writeLocal, doc, applyContent, settleSaveState, latest, pinnedRef, serverJson],
  )

  // Autosave (debounced) after the user changes something.
  useEffect(() => {
    if (!dirty || !hasContent || sending || images.busy || conflict) return
    const t = setTimeout(() => {
      setDirty(false)
      void saveDraft().catch(() => {})
    }, 1500)
    return () => clearTimeout(t)
  }, [dirty, hasContent, sending, images.busy, payload, saveDraft, conflict])

  /** Save now, without waiting for the pause (leaving the form, switching windows, asking the assistant). */
  const saveNow = useCallback(async () => {
    if (doneRef.current || sending || conflictRef.current || !hasContentRef.current) return saveChain.current
    if (dirtyRef.current || unsavedNow() || !draftIdRef.current) {
      setDirty(false)
      return saveDraft().catch(() => {})
    }
    return saveChain.current
  }, [sending, saveDraft, unsavedNow, doneRef])
  useEffect(() => {
    const away = () => {
      if (document.visibilityState === 'hidden' || !document.hasFocus()) void saveNow()
    }
    document.addEventListener('visibilitychange', away)
    window.addEventListener('blur', away)
    return () => {
      document.removeEventListener('visibilitychange', away)
      window.removeEventListener('blur', away)
    }
  }, [saveNow])
  // The assistant panel saves every open editor before it asks, and sends what they show
  // (lib/live/editors.ts); "Open draft" brings this one forward.
  const focusForm = useCallback(() => {
    onFocusRequest?.()
    if (liveKey) useCompose.getState().setFocused(liveKey)
    setTimeout(() => {
      formRef.current?.scrollIntoView({ block: 'nearest' })
      editor?.commands.focus()
      flashEditor()
    }, 0)
  }, [onFocusRequest, liveKey, editor])
  useEffect(
    () =>
      registerEditor(liveKey ?? `form-${localId}`, {
        kind: 'draft',
        id: () => draftIdRef.current,
        flush: async () => {
          await saveNow()
        },
        touched: () => lastTouched.current,
        focus: focusForm,
        snapshot: () => {
          if (!liveKey || doneRef.current) return null
          const p = payloadRef.current(false)
          const st = useCompose.getState()
          const win = st.windows.find((w) => w.key === liveKey)
          return {
            id: liveKey, draft_id: draftIdRef.current, base_version: doc.version,
            dirty: dirtyRef.current || unsavedNow() || !!conflictRef.current,
            focused: st.focusedKey === liveKey || (st.windows.length === 1 && win?.mode !== 'minimized'),
            mode: initial.forward_of_message_id ? 'forward' : initial.in_reply_to_message_id ? ((initial.cc ?? []).length ? 'reply_all' : 'reply') : 'new',
            to: p.to.map((a) => a.email), cc: p.cc.map((a) => a.email), bcc: p.bcc.map((a) => a.email),
            subject: p.subject, body: (editor ? editor.getText({ blockSeparator: '\n\n' }) : '').slice(0, 8000),
            in_reply_to_message_id: initial.in_reply_to_message_id ?? null, conversation_id: conversationId ?? null,
          }
        },
      }),
    [liveKey, localId, saveNow, focusForm, doc, unsavedNow, editor, initial, conversationId, doneRef],
  )
  const conflictMine = () => {
    // ACT draft.resolve_conflict @web.editor @web.reader ("Use mine": saved over theirs, as a new version)
    if (!doc.useMine()) return
    setConflict(null)
    touchedRef.current = true
    setDirty(true)
  }
  const conflictTheirs = () => {
    const t = doc.useTheirs()
    setConflict(null)
    if (t) applyContent(t, { saved: true })
  }

  useEffect(() => {
    onSubjectChange?.(subject)
  }, [subject, onSubjectChange])

  // ----- attachments -----
  useEffect(() => {
    addFilesRef.current = addFiles
  }, [addFiles])


  // ----- send -----
  // ACT draft.send @web.reader, ACT draft.schedule @web.reader (sendAt)
  const send = useCallback(
    async (sendAt?: Date, force = false) => {
      if (sending || sendingRef.current) return
      const recipients = [...to, ...cc, ...bcc]
      if (!recipients.length) {
        setRecipError(tr('compose.composeForm.pleaseSpecifyAtLeastOne'))
        return
      }
      // An entry that is not an address: say which, in which field, and put the focus on its chip
      // (words typed in To became a red chip and Send seemed to do nothing).
      const fields = [[to, tr('common.to')], [cc, tr('common.cc')], [bcc, tr('common.bcc')]] as const
      for (const [list, field] of fields) {
        const bad = list.find((r) => !isValidEmail(r.email))
        if (!bad) continue
        setRecipError(tr('compose.composeForm.notAnEmailAddress', { email: bad.email, field }))
        const chips = formRef.current?.querySelectorAll<HTMLElement>('[data-testid="recip-chip"].invalid') ?? []
        const chip = Array.from(chips).find((c) => c.dataset.email === bad.email)
        chip?.querySelector('button')?.focus()
        return
      }
      if (uploading || images.busy) {
        toast({ message: images.busy ? tr('compose.composeForm.waitForImagesToFinish') : tr('compose.composeForm.waitForAttachmentsToFinish'), error: true })
        return
      }
      // The server refuses a message with no subject, text or file (core/sendgate.py): say so instead
      // of offering a Send that cannot work. With a text or a file and no subject, ask once.
      if (!subject.trim() && isEmptyHtml(body) && !atts.length) {
        toast({ message: tr('compose.composeForm.theMessageIsEmpty'), error: true })
        return
      }
      if (!force && !subject.trim()) {
        setConfirmEmpty({ at: sendAt })
        return
      }
      // What you wrote (not the quoted message) says "attached" but nothing is attached.
      if (!force && atts.length === 0 && mentionsAttachment(body)) {
        setConfirmAttach({ at: sendAt })
        return
      }
      setSending(true)
      sendingRef.current = true
      try {
        await saveChain.current
        const p = payload(true)
        let res: { outbox: Outbox }
        try {
          res = await api.post<{ outbox: Outbox }>('/send', {
            payload: p,
            draft_id: draftIdRef.current,
            send_at: sendAt ? sendAt.toISOString() : null,
          } as unknown as Record<string, unknown>, { idempotencyKey: attempt.current.key() })
        } catch (e) {
          attempt.current.settle(e)
          throw e
        }
        attempt.current.settle()
        const ob = res.outbox
        finished()
        onClose('sent')
        void qc.invalidateQueries({ queryKey: qk.conversations })
        void qc.invalidateQueries({ queryKey: qk.labels })
        void qc.invalidateQueries({ queryKey: ['outbox'] })
        const undo = async () => {
          unwatchSend(ob.id)
          try {
            const r = await api.post<{ payload: ComposePayload; draft_id?: number | null }>(`/outbox/${ob.id}/cancel`)
            openCompose({ payload: r.payload ?? p, draftId: r.draft_id ?? null, conversationId })
            toast({ message: sendAt ? tr('compose.composeForm.scheduledSendCancelled') : tr('compose.composeForm.sendingUndone') })
            void qc.invalidateQueries({ queryKey: qk.conversations })
          } catch (e) {
            toastError(e, tr('compose.composeForm.tooLateToUndo'))
          }
        }
        if (sendAt) {
          toast({ message: tr('compose.composeForm.sendScheduledFor', { optionTime: formatOptionTime(sendAt) }), actionLabel: tr('common.undo'), onAction: () => void undo(), secondaryLabel: tr('compose.composeForm.viewScheduled'), onSecondary: () => nav('/mail/scheduled') })
        } else {
          // "Sending…" (with Undo while the undo window lasts) until the message has left;
          // then "Message sent". A failure says "Not sent" with Retry and Edit.
          const undoMs = Math.max(0, new Date(ob.send_at).getTime() - Date.now())
          if (ob.status === 'awaiting_approval') {
            toast({ message: tr('compose.composeForm.messageIsWaitingForApproval'), duration: 6000 })
          } else {
            const tid = toast({
              message: tr('common.sending'),
              actionLabel: undoMs > 500 ? tr('common.undo') : undefined,
              onAction: undoMs > 500 ? () => void undo() : undefined,
              duration: 0,
            })
            watchSend(ob, tid, (row) => {
              const cid = row.conversation_id ?? ob.conversation_id
              toast({
                message: tr('compose.composeForm.messageSent'),
                actionLabel: cid ? tr('compose.composeForm.viewMessage') : undefined,
                onAction: cid ? () => nav(mailPath('sent', cid)) : undefined,
                duration: 6000,
              })
            }, (row) => {
              toast({
                message: tr('compose.composeForm.notSent', { plainSendError: plainSendError(row.error) }), error: true, duration: 0,
                actionLabel: tr('common.retry'), onAction: () => void outbox.retry(row),
                secondaryLabel: tr('common.edit2'), onSecondary: () => void outbox.edit(row),
              })
            })
          }
          // ACT mail.archive: Send and archive (Settings → Mail) archives the conversation after the send.
          if (conversationId && settings.send_and_archive) void run({ ids: [conversationId], action: 'archive' }, { silent: true })
        }
      } catch (e) {
        toastError(e, tr('compose.composeForm.messageNotSent'))
      } finally {
        setSending(false)
        sendingRef.current = false
      }
    },
    [sending, to, cc, bcc, uploading, images.busy, atts.length, subject, body, payload, onClose, qc, openCompose, conversationId, nav, settings.send_and_archive, run, finished, outbox],
  )
  useEffect(() => {
    sendRef.current = () => void send()
  }, [send])

  const close = async () => {
    // A failed autosave leaves dirty false: the text still needs saving.
    if (hasContent && (dirty || !draftIdRef.current || unsavedNow())) {
      try {
        await saveDraft()
        toast({ message: tr('compose.composeForm.draftSaved') })
      } catch (e) {
        toastError(e, tr('compose.composeForm.draftNotSaved'))
        return
      }
    }
    finished()
    onClose('closed')
  }

  const closeRef = useRef(close)
  useEffect(() => {
    closeRef.current = close
  })
  useEffect(() => {
    registerClose?.(() => closeRef.current())
  }, [registerClose])

  // ACT draft.discard @web.reader
  const discard = async () => {
    // What the form holds, for the toast's Undo (the server forgets a discarded draft at once).
    const kept = hasContent ? payload(false) : null
    atts.forEach((a) => a.abort?.())
    const id = draftIdRef.current
    finished()
    onClose('discarded')
    if (id) {
      try {
        await saveChain.current
        await api.del(`/drafts/${id}`)
        void qc.invalidateQueries({ queryKey: qk.conversations })
      } catch {
        /* ignore */
      }
    }
    // The Undo opens the same message again as a new draft.
    toast(kept ? {
      message: tr('compose.composeForm.draftDiscarded'), actionLabel: tr('common.undo'), duration: 7000,
      onAction: () => void openCompose({ payload: kept, draftId: null, conversationId }),
    } : { message: tr('compose.composeForm.draftDiscarded') })
  }

  const helpMeWrite = async () => {
    if (!aiText.trim() || aiBusy) return
    setAiBusy(true)
    try {
      const res = await api.post<{ payload: Partial<ComposePayload>; edit?: boolean; changes?: { old: string; new: string }[]; memory_ids?: number[] }>('/agent/draft', {
        conversation_id: conversationId ?? null,
        instructions: aiText.trim(),
        mode: initial.in_reply_to_message_id ? 'reply' : initial.forward_of_message_id ? 'forward' : 'new',
        current: payload(false),
        to: to.map((a) => a.email),
      } as unknown as Record<string, unknown>)
      const p = res.payload ?? {}
      const newBody = splitQuote(p.html ?? '').body
      const undo = snapshot()
      const hadText = !isEmptyHtml(body)
      if (newBody) editor?.commands.setContent(asEditorHtml(newBody), { emitUpdate: true })
      if (p.subject && (!subject.trim() || res.edit)) setSubject(p.subject)
      if (p.to?.length && !to.length) setTo(p.to)
      const memoryIds = res.memory_ids ?? []
      if (hadText || memoryIds.length) {
        setAssistNote({
          title: !hadText ? tr('compose.composeForm.helpMeWriteWrote') : res.edit ? tr('compose.composeForm.helpMeWriteEditedYour') : tr('compose.composeForm.helpMeWriteRewroteYour'),
          changes: res.edit ? (res.changes ?? []) : [],
          undo,
          memoryIds,
        })
        if (hadText) flashEditor()
      }
      setAiText('')
      setAiOpen(false)
      touch()
    } catch (e) {
      toastError(e, tr('compose.composeForm.helpMeWriteFailed'))
    } finally {
      setAiBusy(false)
    }
  }


  // A drop the editor takes (an image placed in the body) never reaches onDrop below.
  useEffect(() => {
    if (!dragOver) return
    const end = () => setDragOver(false)
    document.addEventListener('drop', end, true)
    document.addEventListener('dragend', end, true)
    return () => {
      document.removeEventListener('drop', end, true)
      document.removeEventListener('dragend', end, true)
    }
  }, [dragOver])

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length) addFiles(files)
  }

  return (
    <div
      ref={formRef}
      className={`compose-form${flash ? ' assist-flash' : ''}`}
      data-draft-id={draftId ?? undefined}
      onFocusCapture={() => liveKey && useCompose.getState().setFocused(liveKey)}
      onBlur={(e) => {
        // Leaving the form saves at once (not only after the pause).
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) void saveNow()
      }}
      onKeyDown={(e) => {
        // The text editor and the recipient fields send on Ctrl + Enter themselves (and say so by
        // preventing the default): sending again here showed every warning twice.
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !e.defaultPrevented) {
          e.preventDefault()
          void send()
        }
        // Cc and Bcc are out of the Tab path (ComposeHeader): Ctrl + Shift + C and B add them.
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'C' || e.key === 'c' || e.key === 'B' || e.key === 'b')) {
          e.preventDefault()
          if (e.key.toLowerCase() === 'c') setShowCc(true)
          else setShowBcc(true)
        }
        if (e.key === 'Escape' && variant === 'window') {
          e.stopPropagation()
          void close()
        }
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault()
          setDragOver(true)
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragOver(false)
      }}
      onDrop={onDrop}
      style={{ position: 'relative' }}
    >
      {loadingBase && <DraftLoadingNotice />}
      {/* Read-only until the stored copy is known. */}
      <div style={{ display: 'contents' }} inert={loadingBase}>
      <EditingBanner kind="draft" editing={editing} />
      {conflict && <ConflictBar kind="draft" conflict={conflict} onMine={conflictMine} onTheirs={conflictTheirs} />}
      {goneNote && <DraftGoneNotice onDismiss={() => setGoneNote(false)} />}
      {restoredNote && <DraftRestoredNotice onDismiss={() => setRestoredNote(false)} />}
      {me && identities.length === 0 && <NoIdentityNotice />}
      <ComposeHeader
        identities={identities}
        identity={identity}
        onIdentity={(id) => {
          setIdentityId(id)
          touch()
        }}
        to={to}
        cc={cc}
        bcc={bcc}
        onTo={(v) => {
          setTo(v)
          setRecipError(null)
          touch()
        }}
        onCc={(v) => { setCc(v); setRecipError(null); touch() }}
        onBcc={(v) => { setBcc(v); setRecipError(null); touch() }}
        showCc={showCc}
        showBcc={showBcc}
        onShowCc={() => setShowCc(true)}
        onShowBcc={() => setShowBcc(true)}
        autoFocusTo={!autoFocusBody && variant === 'window' && to.length === 0}
        onSubmit={() => void send()}
        showSubject={variant === 'window'}
        subject={subject}
        onSubject={(v) => {
          setSubject(v)
          touch()
        }}
      />
      {recipError && <p className="compose-field-error" role="alert" data-testid="compose-recipient-error">{recipError}</p>}
      {aiOpen && (
        <HelpMeWriteBar
          text={aiText}
          setText={setAiText}
          inputRef={aiRef}
          picker={picker}
          busy={aiBusy}
          hasBody={!isEmptyHtml(body)}
          isReply={!!initial.in_reply_to_message_id}
          onRun={() => void helpMeWrite()}
          onClose={() => setAiOpen(false)}
        />
      )}
      {assistNote && <AssistNoteBar note={assistNote} onUndo={() => restore(assistNote.undo)} onDismiss={() => setAssistNote(null)} />}
      {proposal && <RecipientProposalBar proposal={proposal} current={{ to, cc, bcc }} onAccept={acceptProposal} onReject={() => setProposal(null)} />}
      <EditorArea editor={editor} dropInline={dragOver}>
        <ImageToolbar
          editor={editor}
          fullSize={images.fullSize}
          onFullSize={images.setFullSize}
          onReplace={(pos) => {
            replacePos.current = pos
            replaceRef.current?.click()
          }}
          onMakeAttachment={(pos) => void images.toAttachment(pos)}
        />
      </EditorArea>
      {quote && !showQuote && (
        <div style={{ padding: '0 16px' }}>
          <button type="button" className="quote-toggle show-trimmed" aria-label={tr('compose.composeForm.showTrimmedContent')} data-tip={tr('compose.composeForm.showTrimmedContent')} onClick={() => {
            editor?.chain().focus('end').insertContent(quote).run()
            setShowQuote(true)
            setQuote('')
          }}>
            •••
          </button>
        </div>
      )}
      {identity?.signature_html && includeSig && (
        <div className="editor-wrap signature" style={{ flex: 'none', minHeight: 0, paddingTop: 0, fontSize: 13 }} aria-label={tr('compose.composeForm.signature')}>
          <span className="muted">-- </span>
          <div className="muted" dangerouslySetInnerHTML={{ __html: identity.signature_html }} />
        </div>
      )}
      {dragOver && <div className="dropzone-attach">{tr('compose.composeForm.dropHereToAttach')}</div>}
      {atts.length > 0 && (
        <AttachmentList
          atts={atts}
          onRemove={(a) => {
            a.abort?.()
            setAtts((l) => l.filter((x) => x.key !== a.key))
            touch()
            // An uploaded file can come back (one still uploading stops for good).
            if (a.blob) toast({
              message: tr('compose.composeForm.attachmentRemoved', { filename: a.filename }), actionLabel: tr('common.undo'), duration: 7000,
              onAction: () => {
                setAtts((l) => (l.some((x) => x.key === a.key) ? l : [...l, a]))
                touch()
              },
            })
          }}
        />
      )}
      {showFormat && <FormatBar editor={editor} />}
      <ComposeFooter
        editor={editor}
        sending={sending}
        onSend={(at, force) => void send(at, force)}
        confirmEmpty={confirmEmpty}
        setConfirmEmpty={setConfirmEmpty}
        confirmAttach={confirmAttach}
        setConfirmAttach={setConfirmAttach}
        showFormat={showFormat}
        onToggleFormat={() => setShowFormat(!showFormat)}
        onAddFiles={addFiles}
        onInsertImages={(files) => void images.insert(files)}
        replaceRef={replaceRef}
        onReplaceFile={(f) => {
          if (replacePos.current !== null) void images.replace(replacePos.current, f)
        }}
        templates={templates}
        onInsertTemplate={(t) => void insertTemplate(t)}
        onManageTemplates={() => nav('/settings/mail/writing#templates')}
        hasSignature={!!identity?.signature_html}
        includeSig={includeSig}
        onToggleSig={() => { setIncludeSig(!includeSig); touch() }}
        aiOpen={aiOpen}
        onToggleAi={() => setAiOpen(!aiOpen)}
        onPopOut={variant === 'inline' && onPopOut ? () => {
          if (unsavedNow()) writeLocal()
          onPopOut(payload(false), draftIdRef.current, localId)
        } : undefined}
        saveState={saveState}
        saveError={saveError}
        onDiscard={() => void discard()}
      />
      </div>
    </div>
  )
}

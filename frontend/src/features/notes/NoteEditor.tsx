// The note editor (web.note): the title, the body of a text note (light
// Markdown shows formatted when the field is not focused) or the checklist, link chips, the meta
// line and the footer toolbar. It autosaves 1.5 s after the last change and at once on blur and on
// close, with the version the editor started from (live editing merges; a clash shows the bar).
// A note in Trash opens read-only with Restore and Delete forever.

import { assistantNameNow } from '../rail/assistant/useAssistantName'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive, ArchiveRestore, BellPlus, CalendarDays, CircleCheck, EyeOff, History, ListChecks, Mail, Mic, MoreVertical, Palette, Pin, Redo2, ShoppingCart, Sparkles, SquarePlus, Tag, Type, Undo2, Users, X,
} from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../../api/client'
import { useMe } from '../../api/hooks'
import { MenuHeader, MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { useOnline } from '../../hooks/useOnline'
import { formatListDate } from '../../lib/format'
import { LinkPicker, linkTarget } from './LinkPicker'
import { checkpoint, convertNote, linkNote, markSeen, patchNote, rememberVersion, restoreVersion, setState, uncheckAll, undismissUsual, unlinkNote } from './actions'
import { useNote, useRevision, useRevisions, useUsual } from './api'
import { forgetHistory, useEditorHistory, type Field } from './useEditorHistory'
import { Checklist, type ChecklistHandle } from './Checklist'
import { ColorGrid, NoteMenu } from './menus'
import { Md } from './NoteCard'
import { colorName, mdBlocks, qtyLabel, trashLine } from './model'
import { hereLine, useEditingPerson, useUnsharedBy } from './presence'
import { useHere, usePresenceBeacon } from './presenceBeacon'
import { shareApi, shareKey, sharedLine, viewOnlyLine } from './sharing'
import { ListSyncState } from './ListSyncState'
import { repeatLine } from './repeat'
import { useNotesUI } from './store'
import type { Note, NoteLink } from './types'
import type { AskAbout } from '../assistant-context/askAbout'
import { openAsk, useAskTarget } from '../ask/askStore'
import { useNoteVerbs } from './useVerbs'
import { ReminderChips, RemindMenu, useRemindPrefs } from './remind'
import { CreateFromNote } from './CreateFromNote'
import { t, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

const AUTOSAVE_MS = 1500

interface Props {
  id: string
  focusItem?: string | null
  onClose: () => void
  /** A new note: focus the title (or the add line of a new list). */
  fresh?: boolean
}

export function NoteEditor({ id, focusItem, onClose, fresh }: Props) {
  const q = useNote(id)
  const unsharedBy = useUnsharedBy(id)
  if (q.isError) {
    const gone = q.error instanceof ApiError && q.error.status === 404
    return (
      <div className="nt-editor nt-editor-empty" data-testid="note-editor">
        {/* A note unshared while it is open. */}
        <p>{gone && unsharedBy ? t('notes.noteEditor.stoppedSharingThisNote', { unsharedBy }) : gone ? t('notes.noteEditor.thisNoteIsNotAvailable') : t('notes.noteEditor.theNoteCouldNotBe')}</p>
        <button type="button" className="btn text" onClick={onClose}>{t('common.close')}</button>
      </div>
    )
  }
  if (!q.data) return <div className="nt-editor nt-editor-empty" data-testid="note-editor" aria-busy="true" />
  return <Editor key={id} note={q.data} focusItem={focusItem} onClose={onClose} fresh={fresh} />
}

function Editor({ note, focusItem, onClose, fresh }: { note: Note; focusItem?: string | null; onClose: () => void; fresh?: boolean }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const online = useOnline()
  const verb = useNoteVerbs()
  const trashed = !!note.deleted_at
  // Shift+A and the palette's "Ask about this…" attach the open note.
  useAskTarget(trashed ? null : noteAbout(note, []))
  // Offline, a list keeps working (ticks, new items and edits wait, offline.ts); a text note, the title
  // and the note's own settings wait for the connection.
  const readOnly = trashed || note.level === 'view' || (!online && note.kind !== 'checklist')
  const netReadOnly = readOnly || !online
  const prefs = useRemindPrefs()
  const history = useNotesUI((s) => s.history)
  const setUI = useNotesUI((s) => s.set)
  const [title, setTitle] = useState(note.title)
  const [body, setBody] = useState(note.body)
  const [bodyFocus, setBodyFocus] = useState(false)
  const [saved, setSaved] = useState<'saved' | 'saving' | 'dirty' | null>(null)
  const [conflict, setConflict] = useState<{ fields: Partial<Note> } | null>(null)
  const dirty = useRef<{ title?: string; body?: string }>({})
  const timer = useRef<number | null>(null)
  const titleRef = useRef<HTMLTextAreaElement>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const listRef = useRef<ChecklistHandle>(null)
  const colourA = useAnchor<HTMLButtonElement>()
  const moreA = useAnchor<HTMLButtonElement>()
  const linkA = useAnchor<HTMLButtonElement>()
  const createA = useAnchor<HTMLButtonElement>()
  const remindA = useAnchor<HTMLButtonElement>()

  useEffect(() => rememberVersion(note.id, note.version), [note.id, note.version])
  const me = useMe().data?.user.id
  const editingPerson = useEditingPerson(note.id, me)
  const shared = note.level !== 'owner' || (note.sharing?.count ?? 0) > 0
  // Who else has it open: this tab says it is here, and shows the others.
  usePresenceBeacon('note', note.id, shared && !trashed && online)
  const here = useHere(note.id, me)
  // "Share with Cleo" in the ⋮ menu: the people are loaded with the editor, so the menu opens complete.
  useQuery({ queryKey: shareKey('note', note.id), queryFn: () => shareApi.get('note', note.id), enabled: !trashed && online && (note.level === 'owner' || note.level === 'share'), staleTime: 60_000 })
  // The "changed since you looked" dot goes when the note opens, and again on close (a shared note
  // may change while it is open).
  useEffect(() => {
    if (note.changed) void markSeen(qc, 'note', note.id)
    return () => {
      if (shared) void markSeen(qc, 'note', note.id)
    }
  }, [note.id]) // eslint-disable-line react-hooks/exhaustive-deps
  // A change from elsewhere (another tab, the assistant) shows at once when nothing here is unsaved.
  // One made as you (Ambrose's rewrite, your other tab) is one step of this editor's Undo.
  useEffect(() => {
    const mine = me != null && note.updated_by?.id === me
    for (const [k, v] of [['title', note.title], ['body', note.body]] as const) {
      if (dirty.current[k] !== undefined) continue
      if (mine && values.current[k] !== v && (k === 'title' || note.kind === 'text')) hist.recordText(k, values.current[k], v, 'insertReplacementText')
      values.current = { ...values.current, [k]: v }
    }
    if (dirty.current.title === undefined) setTitle(note.title)
    if (dirty.current.body === undefined) setBody(note.body)
  }, [note.title, note.body]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (fresh) {
      if (note.kind === 'checklist' && note.title) listRef.current?.focusAdd()
      else titleRef.current?.focus()
    }
  }, [fresh]) // eslint-disable-line react-hooks/exhaustive-deps

  // ACT note.edit @web.note
  const flush = async (force = false) => {
    if (timer.current) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    const fields = { ...dirty.current }
    if (fields.title === undefined && fields.body === undefined) return
    dirty.current = {}
    setSaved('saving')
    try {
      await patchNote(qc, note.id, fields, { quiet: true, force })
      setSaved('saved')
      setConflict(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict({ fields })
        setSaved(null)
      } else {
        dirty.current = { ...fields, ...dirty.current }
        setSaved('dirty')
      }
    }
  }
  const change = (k: 'title' | 'body', v: string) => {
    if (k === 'title') setTitle(v)
    else setBody(v)
    dirty.current[k] = v
    setSaved('dirty')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void flush(), AUTOSAVE_MS)
  }
  // Save on close, and end the editing session (a revision). The editor's history goes with it.
  useEffect(() => () => {
    void flush()
    checkpoint(note.id)
    forgetHistory(note.id)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Undo and redo: typing, pastes, cuts and replacements here, and every
  // change made in the editor that offers an undo (ticks, items, sections, colour, labels).
  const values = useRef({ title, body })
  values.current = { title, body }
  const hist = useEditorHistory({
    qc, noteId: note.id, disabled: readOnly,
    kind: () => note.kind,
    read: (f) => values.current[f],
    apply: (f, v, caret) => {
      change(f, v)
      values.current = { ...values.current, [f]: v }
      if (f === 'body' && note.kind === 'text') setBodyFocus(true)
      window.setTimeout(() => {
        const target = f === 'title' ? titleRef.current : bodyRef.current
        if (!target) return
        target.focus()
        target.setSelectionRange(caret, caret)
      }, 0)
    },
  })
  const edit = (k: Field, v: string, inputType?: string | null) => {
    hist.recordText(k, values.current[k], v, inputType)
    values.current = { ...values.current, [k]: v }
    change(k, v)
  }
  const rootRef = useRef<HTMLDivElement>(null)
  // Ctrl/⌘+Z, Ctrl/⌘+Shift+Z and Ctrl+Y go to this history, in the editor and while nothing else
  // has the focus (a ticked row that left the list). A line with text not yet saved (an item being
  // typed, the add line) keeps the browser's own undo for that text.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !(e.ctrlKey || e.metaKey) || e.altKey) return
      const k = e.key.toLowerCase()
      const redo = (k === 'z' && e.shiftKey) || (k === 'y' && e.ctrlKey && !e.shiftKey)
      if (!(k === 'z' && !e.shiftKey) && !redo) return
      const target = e.target as HTMLElement | null
      const inside = !!target && !!rootRef.current?.contains(target)
      if (!inside && target && target !== document.body) return
      if (inside && nativeUndo(target)) return
      e.preventDefault()
      e.stopPropagation()
      if (redo) hist.redo()
      else hist.undo()
    }
    // The browser's Undo and Redo from a text field's menu.
    const onBefore = (e: InputEvent) => {
      if (e.inputType !== 'historyUndo' && e.inputType !== 'historyRedo') return
      if (nativeUndo(e.target as HTMLElement)) return
      e.preventDefault()
      if (e.inputType === 'historyRedo') hist.redo()
      else hist.undo()
    }
    const root = rootRef.current
    window.addEventListener('keydown', onKey, true)
    root?.addEventListener('beforeinput', onBefore)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      root?.removeEventListener('beforeinput', onBefore)
    }
  })

  const close = () => {
    void flush()
    onClose()
  }

  // ACT note.to_checklist @web.note, ACT note.to_text @web.note
  const convert = async () => {
    if (readOnly) return
    await flush()
    await convertNote(qc, note.id, note.kind === 'text' ? 'checklist' : 'text')
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === '8' || e.key === '*' || e.code === 'Digit8')) {
        e.preventDefault()
        void convert()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const tool = (label: string, icon: ReactNode, run: (e: React.MouseEvent<HTMLButtonElement>) => void, opts: { on?: boolean; disabled?: boolean; tip?: string } = {}) => (
    <button type="button" className={`icon-btn sm${opts.on ? ' on' : ''}`} aria-label={label} aria-pressed={opts.on} data-tip={opts.tip ?? label} disabled={opts.disabled} onClick={run}>{icon}</button>
  )

  const byOther = note.updated_by?.id != null && me != null && note.updated_by.id !== me && note.updated_by.name
  const edited = note.updated_at ? t('notes.noteEditor.edited', { value: byOther ? t('notes.noteEditor.by', { updatedByName: note.updated_by!.name }) : '', listDate: formatListDate(note.updated_at) }) : ''
  const sharedText = sharedLine(note)

  return (
    <div ref={rootRef} className={`nt-editor${note.color ? ` nt-c-${note.color}` : ''}`} data-testid="note-editor" data-note={note.id} data-kind={note.kind}
      onKeyDown={(e) => {
        // Keys in the editor's menus (portals) bubble here through React; Esc there closes only the menu.
        if (e.key === 'Escape' && !e.defaultPrevented && e.currentTarget.contains(e.target as Node)) {
          e.preventDefault()
          e.stopPropagation()
          close()
        }
      }}>
      {trashed && (
        <div className="nt-banner" role="status">
          <span>{t('notes.noteEditor.aNoteInTrashCannot', { trashLine: trashLine(note) })}</span>
          <span className="spacer" />
          <button type="button" className="btn text sm" onClick={() => void verb(note, 'restore')}>{t('common.restore')}</button>
          <button type="button" className="btn text sm danger" onClick={() => void verb(note, 'purge')}>{t('common.deleteForever')}</button>
        </div>
      )}
      {note.level === 'view' && !trashed && <div className="nt-banner" role="status" data-testid="note-view-only">{viewOnlyLine(note)}</div>}
      {(editingPerson || here.length > 0) && (
        <div className="nt-banner nt-banner-presence" role="status" data-testid="note-presence">
          <span className="nt-presence">{editingPerson ? t('notes.noteEditor.isEditing', { editingPerson }) : hereLine(here, note.kind === 'checklist' ? 'list' : 'note')}</span>
        </div>
      )}
      {conflict && (
        <div className="nt-banner warn" role="alert" data-testid="note-conflict">
          <span>{t('notes.noteEditor.thisNoteChangedElsewhereWhile')}</span>
          <span className="spacer" />
          <button type="button" className="btn text sm" onClick={() => {
            dirty.current = {}
            setConflict(null)
            setTitle(note.title)
            setBody(note.body)
          }}>{t('common.keepTheirs')}</button>
          <button type="button" className="btn text sm" onClick={() => {
            dirty.current = conflict.fields as { title?: string; body?: string }
            void flush(true)
          }}>{t('common.useMine')}</button>
        </div>
      )}
      <div className="nt-editor-scroll">
        <div className="nt-editor-head">
          <textarea ref={titleRef} className="nt-title" dir="auto" rows={1} value={title} placeholder={t('common.title')} aria-label={t('common.title')} readOnly={netReadOnly} maxLength={500}
            data-history="title"
            onChange={(e) => edit('title', e.target.value.replace(/\n/g, ' '), (e.nativeEvent as InputEvent).inputType)}
            onBlur={() => {
              hist.seal()
              void flush()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                if (note.kind === 'text') bodyRef.current?.focus()
                else listRef.current?.focusAdd()
              }
            }} />
          {!trashed && tool(note.state.pinned ? t('common.unpin') : t('common.pin'), <Pin size={18} fill={note.state.pinned ? 'currentColor' : 'none'} />, () => void verb(note, 'pin'), { on: note.state.pinned, tip: note.state.pinned ? t('common.unpin') : t('common.pin') })}
        </div>
        {note.kind === 'text' ? (
          bodyFocus || readOnly === false && !body ? (
            <textarea ref={bodyRef} className="nt-body" dir="auto" value={body} placeholder={t('notes.noteEditor.takeANote')} aria-label={t('common.note')} readOnly={readOnly} maxLength={20000}
              autoFocus={bodyFocus} onFocus={() => setBodyFocus(true)}
              data-history="body"
              onChange={(e) => edit('body', e.target.value, (e.nativeEvent as InputEvent).inputType)}
              onBlur={() => {
                hist.seal()
                setBodyFocus(false)
                void flush()
              }} />
          ) : (
            <div className="nt-body nt-body-view bidi" tabIndex={0} role="textbox" aria-label={t('common.note')} aria-readonly={readOnly} aria-multiline
              onClick={(e) => {
                if ((e.target as HTMLElement).closest('a')) return
                if (!readOnly) setBodyFocus(true)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !readOnly) {
                  e.preventDefault()
                  setBodyFocus(true)
                }
              }}>
              {mdBlocks(body).map((b, i) => <p key={i} className={b.bullet ? 'nt-bullet' : undefined}><Md parts={b.parts} />{!b.parts.length && ' '}</p>)}
            </div>
          )
        ) : (
          <Checklist ref={listRef} note={note} readOnly={readOnly} focusItem={focusItem} />
        )}
        {!!note.links?.length && (
          <div className="nt-links" aria-label={t('common.links')}>
            {note.links.map((l) => <LinkChip key={l.id} link={l} readOnly={netReadOnly} onOpen={() => nav(linkTarget(l))} onRemove={() => void unlinkNote(qc, note.id, l.id)} />)}
          </div>
        )}
        {/* The viewer's own reminders: the time, × removes it. */}
        <ReminderChips note={note} readOnly={trashed || !online} />
        {note.state.labels.length > 0 && (
          <div className="nt-editor-labels">
            {note.state.labels.map((l) => (
              <span key={l} className="nt-label-chip" dir="auto">{l}{!trashed && online && <button type="button" aria-label={t('notes.noteEditor.removeTheLabel', { l })} onClick={() => void setState(qc, note.id, { remove_labels: [l] }, t('notes.noteEditor.labelRemoved'))}><X size={12} /></button>}</span>
            ))}
          </div>
        )}
        <div className="nt-meta muted" aria-live="polite">
          {!trashed && note.kind === 'checklist' && <ListSyncState note={note} online={online} />}
          {note.repeat?.rule && <span data-testid="note-repeat-line">{repeatLine(note, prefs.zone)} · </span>}
          {sharedText && <span data-testid="note-shared-line">{sharedText} · </span>}
          {saved === 'saving' ? t('common.saving') : saved === 'saved' ? t('common.saved') : saved === 'dirty' ? t('notes.noteEditor.unsavedChanges') : edited}
          {note.updated_at && saved !== null && <span title={note.updated_at}> · {edited}</span>}
        </div>
      </div>
      {history && <HistoryPanel note={note} onClose={() => setUI({ history: false })} />}
      <div className="nt-toolbar" role="toolbar" aria-label={t('common.note')}>
        {!trashed && (
          <>
            {tool(t('common.colour'), <Palette size={18} />, (e) => colourA.toggle(e.currentTarget), { disabled: netReadOnly })}
            {tool(t('common.labels'), <Tag size={18} />, (e) => void verb(note, 'labels', e.currentTarget), { disabled: !online })}
            {tool(note.state.archived ? t('common.unarchive') : t('common.archive'), note.state.archived ? <ArchiveRestore size={18} /> : <Archive size={18} />, () => void verb(note, 'archive'), { disabled: !online })}
            {/* ACT note.remind @web.note: a reminder is the viewer's own, so a view-only sharee sets one too. */}
            {tool(t('notes.noteEditor.reminder'), <BellPlus size={18} />, (e) => remindA.toggle(e.currentTarget), { disabled: !online, on: !!note.reminders?.length, tip: note.reminders?.length ? t('notes.noteEditor.reminderSet') : t('common.remindMe') })}
            {/* ACT note.share @web.note */}
            {tool(note.level === 'owner' || note.level === 'share' ? t('common.share') : t('common.people'), <Users size={18} />, () => setUI({ share: { kind: 'note', id: note.id, name: note.title || (note.kind === 'checklist' ? t('common.untitledList') : t('common.untitledNote')) } }), { tip: sharedText || t('common.share'), disabled: !online })}
            {tool(t('common.link'), <Mail size={18} />, (e) => linkA.toggle(e.currentTarget), { disabled: netReadOnly, tip: t('notes.noteEditor.addALink') })}
            {/* Create from note: Task, Event, Email. A view-only sharee may make them too. */}
            {tool(t('notes.noteEditor.createFromNote'), <SquarePlus size={18} />, (e) => createA.toggle(e.currentTarget), { on: createA.open, disabled: !online })}
            {tool(note.kind === 'text' ? t('notes.noteEditor.makeItAList') : t('notes.noteEditor.makeItText'), note.kind === 'text' ? <ListChecks size={18} /> : <Type size={18} />, () => void convert(), { disabled: netReadOnly, tip: t('notes.noteEditor.ctrlShift8', { value: note.kind === 'text' ? t('notes.noteEditor.makeItAList') : t('notes.noteEditor.makeItText') }) })}
            {note.kind === 'checklist' && tool(t('notes.noteEditor.shoppingList'), <ShoppingCart size={18} />, () => void patchNote(qc, note.id, { shopping: !note.shopping }, { label: note.shopping ? t('notes.noteEditor.shoppingListOff') : t('notes.noteEditor.shoppingListOn') }), { on: note.shopping, disabled: netReadOnly, tip: note.shopping ? t('notes.noteEditor.shoppingListOn2') : t('notes.noteEditor.shoppingListOff2') })}
            {/* ACT note.undo_edit @web.note, ACT note.redo_edit @web.note */}
            {tool(t('common.undo'), <Undo2 size={18} />, () => hist.undo(), { disabled: !hist.canUndo, tip: t('notes.noteEditor.undoShortcut', { keys: modKey('Z') }) })}
            {tool(t('notes.noteEditor.redo'), <Redo2 size={18} />, () => hist.redo(), { disabled: !hist.canRedo, tip: t('notes.noteEditor.redoShortcut', { keys: modKey('Shift+Z') }) })}
            {tool(t('notes.noteEditor.versionHistory'), <History size={18} />, () => setUI({ history: !history }), { on: history, disabled: !online })}
            {tool(t('notes.noteEditor.ask', { name: assistantNameNow() }), <Sparkles size={18} />, (e) => askAbout(note, e.currentTarget), { tip: t('notes.noteEditor.askAboutThisNoteShift', { name: assistantNameNow() }) })}
            {tool(t('common.more'), <MoreVertical size={18} />, (e) => moreA.toggle(e.currentTarget))}
          </>
        )}
        <span className="spacer" />
        <button type="button" className="btn text nt-close" onClick={close}>{t('common.close')}</button>
      </div>
      <Popover anchor={colourA.anchor} open={colourA.open} onClose={colourA.close} role="dialog" label={t('common.colour')} placement="top-start">
        <ColorGrid value={note.color} onPick={(c) => {
          colourA.close()
          void patchNote(qc, note.id, { color: c }, { label: c ? t('notes.noteEditor.colour', { colorName: colorName(c) }) : t('common.defaultColour') })
        }} />
      </Popover>
      <Popover anchor={remindA.anchor} open={remindA.open} onClose={remindA.close} placement="top-start" label={t('common.remindMe')}>
        <RemindMenu note={note} onDone={remindA.close} />
      </Popover>
      <Popover anchor={linkA.anchor} open={linkA.open} onClose={linkA.close} role="dialog" label={t('notes.noteEditor.addALink')} placement="top-start">
        <LinkForm onClose={linkA.close} onLink={(kind, ref) => {
          linkA.close()
          void linkNote(qc, note.id, kind, ref)
        }} />
      </Popover>
      <CreateFromNote note={note} anchor={createA.anchor} open={createA.open} onClose={createA.close} />
      <Popover anchor={moreA.anchor} open={moreA.open} onClose={moreA.close} placement="top-start" label={t('common.more')}>
        {note.kind === 'checklist' && (
          <>
            {/* ACT note.uncheck_all @web.note */}
            <MenuItem icon={<CircleCheck size={16} />} disabled={netReadOnly} onSelect={() => {
              moreA.close()
              void uncheckAll(qc, note.id)
            }}>{t('notes.noteEditor.uncheckAll')}</MenuItem>
            <MenuItem checked={note.checked_at_bottom} disabled={netReadOnly} onSelect={() => {
              moreA.close()
              void patchNote(qc, note.id, { checked_at_bottom: !note.checked_at_bottom }, { label: note.checked_at_bottom ? t('notes.noteEditor.checkedItemsStayInPlace') : t('notes.noteEditor.checkedItemsAtTheBottom') })
            }}>{t('notes.noteEditor.showCheckedAtTheBottom')}</MenuItem>
            <MenuItem checked={note.suggest_usual} disabled={netReadOnly} onSelect={() => {
              moreA.close()
              void patchNote(qc, note.id, { suggest_usual: !note.suggest_usual }, { label: note.suggest_usual ? t('notes.noteEditor.usualItemsOff') : t('notes.noteEditor.usualItemsOn') })
            }}>{t('notes.noteEditor.suggestUsualItems')}</MenuItem>
            <HiddenSuggestions note={note} onDone={moreA.close} />
            {!netReadOnly && (
              <>
                {/* ACT checksection.order_mode @web.note: the order of the items above the sections (the top group). */}
                <MenuHeader>{(note.sections ?? []).length ? t('notes.noteEditor.orderOfTheItemsAbove') : t('notes.noteEditor.orderOfTheItems')}</MenuHeader>
                <MenuItem selected={(note.top_order_mode ?? note.top?.order_mode) === 'learned'} onSelect={() => {
                  moreA.close()
                  void patchNote(qc, note.id, { top_order_mode: 'learned' }, { label: t('notes.noteEditor.learnedOrder') })
                }}>{t('notes.noteEditor.learned', { value: note.top?.trips ? t('notes.noteEditor.fromTrips', { count: note.top.trips }) : '' })}</MenuItem>
                <MenuItem selected={(note.top_order_mode ?? note.top?.order_mode) !== 'learned'} onSelect={() => {
                  moreA.close()
                  void patchNote(qc, note.id, { top_order_mode: 'manual' }, { label: t('common.manualOrder') })
                }}>{t('common.manual')}</MenuItem>
              </>
            )}
            <MenuSep />
          </>
        )}
        <NoteMenu note={note} inEditor online={online} onClose={moreA.close} onVerb={(v) => {
          moreA.close()
          if (v === 'history') setUI({ history: true })
          else void verb(note, v).then(() => (v === 'delete' || v === 'leave') && onClose())
        }} />
      </Popover>
    </div>
  )
}

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
/** "Ctrl+Z", or "⌘Z" on a Mac. */
function modKey(k: string): string {
  return MAC ? `⌘${k.replace('Shift+', '⇧')}` : `Ctrl+${k}`
}

/** A text field whose unsaved text keeps the browser's own undo: any field but the title and the
 *  body, unless it says it has nothing unsaved (`data-history`). */
function nativeUndo(el: HTMLElement | null): boolean {
  if (!el) return false
  const textField = el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && !['checkbox', 'radio', 'button', 'submit'].includes(el.type))
  return textField && !el.dataset.history
}

/** The note as the item to ask about, with the items the text selection spans in a checklist. */
export function noteAbout(note: Note, items: string[] = selectedItems()): AskAbout {
  return { kind: 'note', id: note.id, title: note.title, list: note.kind === 'checklist', ...(items.length ? { items } : {}) }
}

/** "Ask assistant" in the note editor: the ask capture opens with this note
 *  attached, and with the selected items ("Turn these into tasks").
 *  ACT agent.ask_about @web.note */
export function askAbout(note: Note, anchor?: Element | null): void {
  openAsk(noteAbout(note), anchor)
}

/** The checklist items a text selection in the editor touches (`li[data-item]` rows), in order. */
export function selectedItems(sel: Selection | null = typeof window === 'undefined' ? null : window.getSelection()): string[] {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return []
  const range = sel.getRangeAt(0)
  const rows = document.querySelectorAll<HTMLElement>('.nt-editor li[data-item]')
  const out: string[] = []
  rows.forEach((li) => {
    if (range.intersectsNode(li) && li.dataset.item) out.push(li.dataset.item)
  })
  return out.slice(0, 50)
}

// ---------------------------------------------------------------- links

export { linkTarget, parseLink } from './LinkPicker'

const LINK_ICON: Record<NoteLink['target_kind'], ReactNode> = {
  conversation: <Mail size={14} />, event: <CalendarDays size={14} />, task: <CircleCheck size={14} />, recording: <Mic size={14} />,
}

// ACT note.unlink @web.note
export function LinkChip({ link, readOnly, onOpen, onRemove }: { link: NoteLink; readOnly: boolean; onOpen: () => void; onRemove: () => void }) {
  const hidden = link.state === 'hidden' || link.title == null
  const title = hidden
    ? (link.target_kind === 'conversation' ? t('notes.noteEditor.emailNotSharedWithYou') : t('tables.cells.notSharedWithYou'))
    : link.title!
  return (
    <span className="nt-link-chip" data-link-kind={link.target_kind}>
      <button type="button" onClick={onOpen}>{LINK_ICON[link.target_kind]} {title}</button>
      {!readOnly && <button type="button" aria-label={t('notes.noteEditor.removeTheLinkTo', { title })} data-tip={t('notes.noteEditor.removeLink')} onClick={onRemove}><X size={12} /></button>}
    </span>
  )
}

/** The link form of a note or a table: the search picker (LinkPicker.tsx). */
// ACT note.link @web.note
export function LinkForm({ onLink, onClose }: { onLink: (kind: NoteLink['target_kind'], ref: Record<string, unknown>) => void; onClose?: () => void }) {
  return <LinkPicker onLink={onLink} onClose={onClose} />
}

// ---------------------------------------------------------------- usual: hidden suggestions

function HiddenSuggestions({ note, onDone }: { note: Note; onDone: () => void }) {
  const qc = useQueryClient()
  const usual = useUsual(note.id, note.suggest_usual)
  const hidden = usual.data?.hidden ?? []
  if (!hidden.length) return null
  return (
    <>
      <MenuHeader>{t('notes.noteEditor.hiddenSuggestions')}</MenuHeader>
      {hidden.map((h) => (
        <MenuItem key={h.key} icon={<EyeOff size={16} />} onSelect={() => {
          onDone()
          void undismissUsual(qc, note.id, h.key)
        }}>{t('notes.noteEditor.suggestAgain', { text: h.text, value: h.until ? '' : t('notes.noteEditor.neverSuggested') })}</MenuItem>
      ))}
    </>
  )
}

// ---------------------------------------------------------------- version history

const SOURCE: Record<string, string> = { edit: '', get assistant() { return t('notes.noteEditor.text', { name: assistantNameNow() }) }, agent: t('notes.noteEditor.agent'), restore: t('common.restore'), import: t('notes.noteEditor.import'), convert: t('notes.noteEditor.convert'), repeat: t('common.repeat') }

/** ⋮ → Version history: revisions by day with their author or source and a one-line summary. A
 * selected one shows read-only, with what differs from now marked; "Restore this version". */
// ACT note.history @web.note, ACT note.restore_version @web.note
function HistoryPanel({ note, onClose }: { note: Note; onClose: () => void }) {
  const qc = useQueryClient()
  const revs = useRevisions(note.id, true)
  const me = useMe().data?.user.id
  const [sel, setSel] = useState<number | null>(null)
  const one = useRevision(note.id, sel)
  const byDay = new Map<string, typeof list>()
  const list = revs.data ?? []
  for (const r of list) {
    const d = new Date(r.at).toLocaleDateString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric' })
    byDay.set(d, [...(byDay.get(d) ?? []), r])
  }
  const current = new Set((note.items ?? []).map((i) => `${i.checked ? 'x' : ' '}${i.text}`))
  const curBody = new Set(note.body.split('\n'))
  return (
    <aside className="nt-history" aria-label={t('notes.noteEditor.versionHistory')} data-testid="note-history">
      <div className="nt-history-head">
        <h3>{t('notes.noteEditor.versionHistory')}</h3>
        <button type="button" className="icon-btn sm" aria-label={t('notes.noteEditor.closeVersionHistory')} onClick={onClose}><X size={18} /></button>
      </div>
      <div className="nt-history-body">
        <ol className="nt-history-list">
          {revs.isLoading && <li className="muted">{t('common.loading2')}</li>}
          {!revs.isLoading && !list.length && <li className="muted">{t('notes.noteEditor.noEarlierVersionsYet')}</li>}
          {[...byDay.entries()].map(([day, rs]) => (
            <li key={day}>
              <div className="nt-history-day">{day}</div>
              <ul>
                {rs.map((r) => (
                  <li key={r.rev}>
                    <button type="button" className={`nt-history-rev${sel === r.rev ? ' active' : ''}`} aria-pressed={sel === r.rev} onClick={() => setSel(r.rev)}>
                      <span>{new Date(r.at).toLocaleTimeString(appLocale(), { hour: 'numeric', minute: '2-digit', ...clockOpts() })} · {(r.source === 'assistant' ? assistantNameNow() : SOURCE[r.source]) || (r.by?.id != null && r.by.id === me ? t('common.you') : r.by?.name) || t('common.you')}</span>
                      <span className="muted">{r.summary}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
        {sel != null && (
          <div className="nt-history-view" aria-label={t('notes.noteEditor.thisVersion')}>
            {!one.data ? <p className="muted">{t('common.loading2')}</p> : (
              <>
                {one.data.content?.title && <h4 dir="auto">{one.data.content.title}</h4>}
                {one.data.content?.kind === 'text' ? (
                  <div className="nt-history-text bidi">{(one.data.content.body ?? '').split('\n').map((l, i) => <p key={i} className={curBody.has(l) ? undefined : 'diff'}>{l || ' '}</p>)}</div>
                ) : (
                  <ul className="nt-history-items">
                    {(one.data.content?.items ?? []).map((i) => (
                      <li key={i.id} className={current.has(`${i.checked ? 'x' : ' '}${i.text}`) ? undefined : 'diff'}>{i.checked ? '☑' : '☐'} {qtyLabel(i.qty) ? `${qtyLabel(i.qty)} ` : ''}{i.text}</li>
                    ))}
                  </ul>
                )}
                <p className="muted nt-small">{t('notes.noteEditor.markedLinesDifferFromThe')}</p>
                <button type="button" className="btn filled sm" disabled={!!note.deleted_at} onClick={() => {
                  void restoreVersion(qc, note.id, sel).then(() => setSel(null))
                }}>{t('notes.noteEditor.restoreThisVersion')}</button>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}

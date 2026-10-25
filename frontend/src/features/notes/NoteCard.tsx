// A note as a card in the grid: the title, the first 8 unchecked items of a
// checklist (with quantity chips and section names in small capitals) or up to 10 lines of a text
// note, "+ 6 more · 4 checked", the viewer's labels, the link and local-only icons, the initials of
// the other people on a shared note and the dot of a change since this viewer looked. The card's
// background is its colour token. Hover actions: Pin, Colour, Labels, Archive and ⋮. A checkbox on a
// card checks in place, with the undo toast.

import { useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, Bell, BellPlus, Link2, Lock, MoreVertical, Palette, Pin, Repeat, Tag, Trash2, Undo2 } from 'lucide-react'
import { repeatPhrase, repeatWords } from './repeatModel'
import { memo, useState, type DragEvent, type KeyboardEvent, type MouseEvent } from 'react'
import { Popover, useAnchor } from '../../components/Popover'
import { useOnline } from '../../hooks/useOnline'
import { patchItem, patchNote } from './actions'
import { ColorGrid, NoteMenu } from './menus'
import { reminderChip, RemindMenu, useRemindPrefs } from './remind'
import { CardPeople, hasPeople } from './sharing'
import { colorName, mdBlocks, qtyLabel, trashLine, type MdPart } from './model'
import type { Item, Note } from './types'
import { useNoteVerbs } from './useVerbs'
import { t } from '../../i18n'

export function Md({ parts }: { parts: MdPart[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.kind === 'bold' ? <strong key={i}>{p.text}</strong>
          : p.kind === 'italic' ? <em key={i}>{p.text}</em>
            : p.kind === 'link' ? <a key={i} href={p.href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>{p.text}</a>
              : <span key={i}>{p.text}</span>,
      )}
    </>
  )
}

export interface CardProps {
  note: Note
  focused: boolean
  selected: boolean
  open: boolean
  /** Manual sort: the card can be dragged to a new place. */
  draggable: boolean
  onOpen: () => void
  onSelect: () => void
  onDropOn: (dragged: string, where: 'before' | 'after') => void
  /** The card took the keyboard focus (Tab, a click): it becomes the focused card of the keys. */
  onFocusCard?: () => void
}

/** A card re-renders only when its note or its state changes: the grid holds up to 2000 of them,
 *  and a change to one note re-rendered every card (its handlers are stable per note, NotesApp). */
export const NoteCard = memo(NoteCardImpl)

function NoteCardImpl({ note, focused, selected, open, draggable, onOpen, onSelect, onDropOn, onFocusCard }: CardProps) {
  const qc = useQueryClient()
  const verb = useNoteVerbs()
  const colour = useAnchor<HTMLButtonElement>()
  const more = useAnchor<HTMLButtonElement>()
  const remind = useAnchor<HTMLButtonElement>()
  const rp = useRemindPrefs()
  const online = useOnline()
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const trashed = !!note.deleted_at
  const preview = note.preview
  const title = note.title.trim()

  const onKey = (e: KeyboardEvent) => {
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onOpen()
    }
  }
  const stop = (e: MouseEvent) => e.stopPropagation()

  // ACT checkitem.check @web.notes
  const check = (id: string) => {
    const item = (note.items?.find((i) => i.id === id) ?? { id, checked: false, version: undefined }) as Item
    void patchItem(qc, note.id, item, { checked: true })
  }

  const onDragOver = (e: DragEvent) => {
    if (!draggable || !e.dataTransfer.types.includes('application/x-mailr-note')) return
    e.preventDefault()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setDrop(e.clientY < r.top + r.height / 2 ? 'before' : 'after')
  }

  return (
    <article
      className={`nt-card${note.color ? ` nt-c-${note.color}` : ''}${focused ? ' focused' : ''}${selected ? ' selected' : ''}${open ? ' is-open' : ''}${drop ? ` drop-${drop}` : ''}`}
      data-note={note.id}
      data-kind={note.kind}
      tabIndex={0}
      // An article, not a button: the card holds its own checkboxes and buttons. Enter and a
      // click still open it.
      aria-label={`${title || (note.kind === 'checklist' ? t('common.untitledList') : t('common.untitledNote'))}${note.color ? `, ${colorName(note.color)}` : ''}${note.state.pinned ? t('notes.noteCard.pinnedSuffix') : ''}`}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.setData('application/x-mailr-note', note.id)
        e.dataTransfer.effectAllowed = 'move'
      }}
      onDragOver={onDragOver}
      onDragLeave={() => setDrop(null)}
      onDrop={(e) => {
        const id = e.dataTransfer.getData('application/x-mailr-note')
        const where = drop
        setDrop(null)
        if (id && id !== note.id && where) {
          e.preventDefault()
          onDropOn(id, where)
        }
      }}
      // Clicks in the card's menus (portals) bubble here through React; only the card itself opens the note.
      onClick={(e) => e.currentTarget.contains(e.target as Node) && onOpen()}
      onKeyDown={onKey}
      onFocus={(e) => e.target === e.currentTarget && onFocusCard?.()}
    >
      <button type="button" className={`nt-card-select icon-btn sm${selected ? ' on' : ''}`} aria-label={selected ? t('common.deselect') : t('common.select')} aria-pressed={selected} data-tip={t('notes.noteCard.selectX')} onClick={(e) => {
        stop(e)
        onSelect()
      }}>
        <span className="nt-tick" aria-hidden />
      </button>
      {!trashed && (
        <button type="button" className={`nt-card-pin icon-btn sm${note.state.pinned ? ' on' : ''}`} aria-label={note.state.pinned ? t('common.unpin') : t('common.pin')} data-tip={note.state.pinned ? t('notes.noteCard.unpinF') : t('notes.noteCard.pinF')} onClick={(e) => {
          stop(e)
          void verb(note, 'pin')
        }}>
          {note.state.pinned ? <Pin size={18} fill="currentColor" /> : <Pin size={18} />}
        </button>
      )}
      {title && <h3 className="nt-card-title" dir="auto">{title}</h3>}
      {note.kind === 'text' ? (
        <div className="nt-card-body bidi">
          {mdBlocks(note.body.split('\n').slice(0, 10).join('\n')).map((b, i) => (
            <p key={i} className={b.bullet ? 'nt-bullet' : undefined}><Md parts={b.parts} />{!b.parts.length && ' '}</p>
          ))}
          {!title && !note.body.trim() && <p className="muted">{t('notes.noteCard.emptyNote')}</p>}
        </div>
      ) : (
        <ul className="nt-card-items" aria-label={t('notes.noteCard.items')}>
          {(preview?.items ?? []).map((i, n, all) => {
            const head = i.section && (n === 0 || all[n - 1].section !== i.section) ? i.section : null
            return (
              <li key={i.id}>
                {head && <span className="nt-card-section" dir="auto">{head}</span>}
                <span className="nt-card-item">
                  <input type="checkbox" className="checkbox hit24" aria-label={t('notes.noteCard.check', { text: i.text })} disabled={trashed || note.level === 'view'} onClick={stop} onChange={() => check(i.id)} />
                  {qtyLabel(i.qty) && <span className="nt-qty">{qtyLabel(i.qty)}</span>}
                  <span className="nt-item-text" dir="auto">{i.text}</span>
                </span>
              </li>
            )
          })}
          {(preview?.more || preview?.checked) ? (
            <li className="nt-card-more">
              {[preview.more ? t('notes.noteCard.more', { more: preview.more }) : '', preview.checked ? t('notes.noteCard.checked', { checked: preview.checked }) : ''].filter(Boolean).join(' · ')}
            </li>
          ) : null}
          {!preview?.items.length && !preview?.more && !preview?.checked && !title && <li className="muted">{t('notes.noteCard.emptyList')}</li>}
        </ul>
      )}
      {(note.state.labels.length > 0 || note.links?.length || note.local_only || hasPeople(note) || !!note.reminders?.length || !!note.repeat?.rule) && (
        <div className="nt-card-foot">
          {/* The viewer's next reminder: its time, or "Snoozed until …". */}
          {!!note.reminders?.length && (
            <span className={`nt-label-chip nt-remind-chip${note.reminders.every((r) => r.state === 'fired') ? ' fired' : ''}`} data-testid="card-reminder">
              <Bell size={12} aria-hidden /> {reminderChip(note.reminders.find((r) => r.state !== 'fired') ?? note.reminders[note.reminders.length - 1], new Date(), rp)}
            </span>
          )}
          {note.state.labels.map((l) => <span key={l} className="nt-label-chip" dir="auto">{l}</span>)}
          <span className="spacer" />
          <CardPeople item={note} />
          {!!note.links?.length && <Link2 size={14} aria-label={t('notes.noteCard.hasLinks')} />}
          {!!note.repeat?.rule && <Repeat size={14} aria-label={t('notes.noteCard.repeats', { repeatPhrase: repeatPhrase(note.repeat.rule) })} data-tip={repeatWords(note.repeat.rule)} />}
          {note.local_only && <Lock size={14} aria-label={t('common.localOnly2')} />}
        </div>
      )}
      {trashed && <div className="nt-card-trash muted">{trashLine(note)}</div>}
      <div className="nt-card-actions" onClick={stop}>
        {trashed ? (
          <>
            <button type="button" className="icon-btn sm" aria-label={t('common.restore')} data-tip={t('common.restore')} onClick={() => void verb(note, 'restore')}><Undo2 size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={t('common.deleteForever')} data-tip={t('common.deleteForever')} onClick={() => void verb(note, 'purge')}><Trash2 size={18} /></button>
          </>
        ) : (
          <>
            {/* ACT note.remind @web.notes */}
            <button type="button" className="icon-btn sm" aria-label={t('common.remindMe')} data-tip={t('common.remindMe')} onClick={(e) => remind.toggle(e.currentTarget)}><BellPlus size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={t('common.colour')} data-tip={t('common.colour')} onClick={(e) => colour.toggle(e.currentTarget)}><Palette size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={t('common.labels')} data-tip={t('notes.noteCard.labelsT')} onClick={(e) => void verb(note, 'labels', e.currentTarget)}><Tag size={18} /></button>
            <button type="button" className="icon-btn sm" aria-label={note.state.archived ? t('common.unarchive') : t('common.archive')} data-tip={note.state.archived ? t('notes.noteCard.unarchiveE') : t('notes.noteCard.archiveE')} onClick={() => void verb(note, 'archive')}>
              {note.state.archived ? <ArchiveRestore size={18} /> : <Archive size={18} />}
            </button>
            <button type="button" className="icon-btn sm" aria-label={t('common.more')} data-tip={t('common.more')} onClick={(e) => more.toggle(e.currentTarget)}><MoreVertical size={18} /></button>
          </>
        )}
      </div>
      <Popover anchor={colour.anchor} open={colour.open} onClose={colour.close} role="dialog" label={t('common.colour')} placement="bottom-start">
        <ColorGrid value={note.color} onPick={(c) => {
          colour.close()
          void patchNote(qc, note.id, { color: c }, { label: c ? t('notes.noteCard.colour', { colorName: colorName(c) }) : t('common.defaultColour') })
        }} />
      </Popover>
      <Popover anchor={remind.anchor} open={remind.open} onClose={remind.close} placement="bottom-start" label={t('common.remindMe')}>
        <RemindMenu note={note} onDone={remind.close} />
      </Popover>
      <Popover anchor={more.anchor} open={more.open} onClose={more.close} placement="bottom-end" label={t('common.more')}>
        <NoteMenu note={note} online={online} onVerb={(v) => {
          const a = more.anchor
          more.close()
          void verb(note, v, a)
        }} />
      </Popover>
    </article>
  )
}

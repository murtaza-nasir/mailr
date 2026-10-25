// The small menus of a card and the editor: colour (with names), labels (a checklist with
// a filter line and "Create 'name'"), and the ⋮ menu. The same menus serve the card's hover actions,
// the editor's toolbar and the `t` key.

import { Archive, ArchiveRestore, Check, Copy, Download, History, LayoutTemplate, ListChecks, LogOut, Pin, PinOff, Plus, Tag, Trash2, Type, Users } from 'lucide-react'
import { useState } from 'react'
import { MenuHeader, MenuItem, MenuSep } from '../../components/Popover'
import { LocalOnlyItem } from './localOnly'
import { NOTE_COLORS } from './model'
import { QuickShareItems } from './quickShare'
import { RepeatMenuItem } from './repeat'
import type { Note, NoteLabel } from './types'
import { t as tr } from '../../i18n'

/** The 12 swatches and Default, each with its name (colour is never the only signal). */
// ACT note.color @web.notes @web.note
export function ColorGrid({ value, onPick }: { value: string | null; onPick: (c: string | null) => void }) {
  return (
    <div className="nt-colors" role="radiogroup" aria-label={tr('common.colour')}>
      <button type="button" role="radio" aria-checked={!value} className="nt-swatch nt-swatch-default" data-tip={tr('common.default')} aria-label={tr('common.default')} onClick={() => onPick(null)}>
        {!value && <Check size={14} aria-hidden />}
      </button>
      {NOTE_COLORS.map((c) => (
        <button key={c.id} type="button" role="radio" aria-checked={value === c.id} className={`nt-swatch nt-c-${c.id}`} data-tip={c.name} aria-label={c.name} onClick={() => onPick(c.id)}>
          {value === c.id && <Check size={14} aria-hidden />}
        </button>
      ))}
    </div>
  )
}

/** The viewer's labels on one or more notes: each on, off or mixed; a typed name creates one. */
// ACT note.label @web.notes @web.note, ACT notelabel.create @web.notes @web.note
export function LabelsMenu({ notes, labels, onAdd, onRemove }: { notes: Pick<Note, 'state'>[]; labels: NoteLabel[]; onAdd: (name: string) => void; onRemove: (name: string) => void }) {
  const [text, setText] = useState('')
  const t = text.trim()
  const state = (name: string): boolean | 'mixed' => {
    const n = notes.filter((x) => x.state.labels.some((l) => l.toLowerCase() === name.toLowerCase())).length
    return n === 0 ? false : n === notes.length ? true : 'mixed'
  }
  const names = [...new Set([...labels.map((l) => l.name), ...notes.flatMap((n) => n.state.labels)])]
  return (
    <>
      <MenuHeader>{tr('notes.menus.labelNote')}</MenuHeader>
      <div className="tk-menu-input nt-menu-input">
        <Tag size={14} aria-hidden />
        <input value={text} placeholder={tr('notes.menus.enterLabelName')} aria-label={tr('notes.menus.labelName')} data-autofocus maxLength={50}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter' && t) {
              const hit = names.find((n) => n.toLowerCase() === t.toLowerCase())
              if (hit && state(hit) === true) onRemove(hit)
              else onAdd(hit ?? t)
              setText('')
            }
          }} />
      </div>
      {names.filter((n) => !t || n.toLowerCase().includes(t.toLowerCase())).map((n) => {
        const s = state(n)
        return <MenuItem key={n} checked={s} onSelect={() => (s === true ? onRemove(n) : onAdd(n))}>{n}</MenuItem>
      })}
      {t && !names.some((n) => n.toLowerCase() === t.toLowerCase()) && (
        <MenuItem icon={<Plus size={16} />} onSelect={() => {
          onAdd(t)
          setText('')
        }}>{tr('notes.menus.create', { t })}</MenuItem>
      )}
    </>
  )
}

export type NoteVerb = 'pin' | 'archive' | 'duplicate' | 'copy_text' | 'copy_list' | 'history' | 'export' | 'delete' | 'restore' | 'purge' | 'labels' | 'open' | 'share' | 'leave' | 'save_template' | 'local_only' | 'repeat'

/** A sharee may remove a note from their own notes; a group's member leaves the group instead. */
export const canLeave = (n: Pick<Note, 'level' | 'owner'>) => n.level !== 'owner' && n.owner?.kind !== 'group'

/** The ⋮ menu of a card or the editor. */
export function NoteMenu({ note, onVerb, inEditor, online = true, onClose }: { note: Note; onVerb: (v: NoteVerb) => void; inEditor?: boolean; online?: boolean; onClose?: () => void }) {
  if (note.deleted_at) {
    return (
      <>
        <MenuItem icon={<ArchiveRestore size={16} />} onSelect={() => onVerb('restore')}>{tr('common.restore')}</MenuItem>
        <MenuItem icon={<Trash2 size={16} />} danger onSelect={() => onVerb('purge')}>{tr('common.deleteForever')}</MenuItem>
      </>
    )
  }
  return (
    <>
      {!inEditor && (
        <>
          <MenuItem icon={note.state.pinned ? <PinOff size={16} /> : <Pin size={16} />} shortcut="f" onSelect={() => onVerb('pin')}>{note.state.pinned ? tr('common.unpin') : tr('common.pin')}</MenuItem>
          <MenuItem icon={note.state.archived ? <ArchiveRestore size={16} /> : <Archive size={16} />} shortcut="e" onSelect={() => onVerb('archive')}>{note.state.archived ? tr('common.unarchive') : tr('common.archive')}</MenuItem>
          <MenuItem icon={<Tag size={16} />} shortcut="t" onSelect={() => onVerb('labels')}>{tr('notes.menus.labels')}</MenuItem>
          <MenuSep />
        </>
      )}
      {/* ACT note.share @web.notes @web.note: the people dialog (sharing.tsx) */}
      <MenuItem icon={<Users size={16} />} onSelect={() => onVerb('share')}>{note.level === 'owner' || note.level === 'share' ? tr('common.share2') : tr('notes.menus.people')}</MenuItem>
      {/* "Share with Cleo": in the editor, where the people are loaded when it opens. */}
      {inEditor && online && <QuickShareItems kind="note" id={note.id} level={note.level} onDone={onClose} />}
      {/* ACT note.leave @web.notes @web.note */}
      {canLeave(note) && <MenuItem icon={<LogOut size={16} />} onSelect={() => onVerb('leave')}>{tr('notes.menus.removeFromMyNotes')}</MenuItem>}
      <LocalOnlyItem item={note} noun={note.kind === 'checklist' ? 'list' : 'note'} online={online} onToggle={() => onVerb('local_only')} />
      <RepeatMenuItem note={note} onOpen={() => onVerb('repeat')} />
      <MenuSep />
      {/* ACT note.duplicate @web.notes @web.note */}
      <MenuItem icon={<Copy size={16} />} onSelect={() => onVerb('duplicate')}>{tr('notes.menus.makeACopy')}</MenuItem>
      {note.kind === 'text'
        ? <MenuItem icon={<ListChecks size={16} />} onSelect={() => onVerb('copy_list')}>{tr('notes.menus.makeACopyAsA')}</MenuItem>
        : <MenuItem icon={<Type size={16} />} onSelect={() => onVerb('copy_text')}>{tr('notes.menus.makeACopyAsText')}</MenuItem>}
      {/* ACT note.save_template @web.notes @web.note */}
      <MenuItem icon={<LayoutTemplate size={16} />} onSelect={() => onVerb('save_template')}>{tr('common.saveAsTemplate')}</MenuItem>
      {/* ACT note.history @web.note */}
      <MenuItem icon={<History size={16} />} onSelect={() => onVerb('history')}>{tr('notes.menus.versionHistory')}</MenuItem>
      {/* ACT note.export @web.note */}
      <MenuItem icon={<Download size={16} />} onSelect={() => onVerb('export')}>{tr('notes.menus.exportMarkdown')}</MenuItem>
      {note.can_delete !== false && (
        <>
          <MenuSep />
          {/* ACT note.delete @web.notes @web.note */}
          <MenuItem icon={<Trash2 size={16} />} shortcut="#" danger onSelect={() => onVerb('delete')}>{tr('common.delete2')}</MenuItem>
        </>
      )}
    </>
  )
}

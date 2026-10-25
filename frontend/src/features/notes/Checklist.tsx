// Checklist editing: the top group and the sections with their headers
// and menus, rows with a checkbox, a quantity chip, the text edited in place and the extra line, an
// add line per group (the item grammar, with its chips and the paste preview), the usual strip under
// the top add line, and the "n checked" fold. Every drag has a key and a menu equivalent.

import { useQueryClient } from '@tanstack/react-query'
import { useMe } from '../../api/hooks'
import { ChevronDown, ChevronRight, CircleCheck, GripVertical, ListTodo, Minus, MoreVertical, Plus, Trash2, X } from 'lucide-react'
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { MenuHeader, MenuItem, MenuSep, Popover, SubMenu, useAnchor } from '../../components/Popover'
import { parseItems, type ItemToken } from '../../lib/itemadd'
import { taskPath } from '../../lib/routes'
import { toast } from '../../stores/ui'
import { addItems, addUsual, adoptPrefixed, clearChecked, createSection, deleteItem, deleteSection, dismissUsual, itemToTask, moveItem, patchItem, patchSection, prefixedItems } from './actions'
import { runUndo, useUsual } from './api'
import { authorLine, checklistView, flatOrder, qtyLabel, stepQty, type Group } from './model'
import { useNotesUI } from './store'
import { useNotesQueue } from './offline'
import { useTouchedBy } from './presenceBeacon'
import type { Item, Note, Section } from './types'
import { t as tr } from '../../i18n'

const CHECK_LINGER_MS = 600

interface Props {
  note: Note
  readOnly: boolean
  /** #i-<item>: the item to focus once. */
  focusItem?: string | null
}

export interface ChecklistHandle {
  /** Focus the first add line (a new list). */
  focusAdd: () => void
}

export const Checklist = forwardRef<ChecklistHandle, Props>(function Checklist({ note, readOnly, focusItem }, ref) {
  const qc = useQueryClient()
  const sections = useMemo(() => note.sections ?? [], [note.sections])
  const items = useMemo(() => note.items ?? [], [note.items])
  // A check strikes the row first and moves it into the fold 600 ms later, so a mis-click can be
  // undone in place.
  const [lingering, setLingering] = useState<Set<string>>(new Set())
  const view = useMemo(() => checklistView(note, sections, items, lingering), [note, sections, items, lingering])
  const order = useMemo(() => flatOrder(view), [view])
  const inputs = useRef(new Map<string, HTMLInputElement>())
  const topAdd = useRef<HTMLInputElement>(null)
  const [insertAfter, setInsertAfter] = useState<string | null>(null)
  const [foldOpen, setFoldOpen] = useState(false)
  const [newSectionAfter, setNewSectionAfter] = useState<{ after: string | null } | null>(null)
  const [deleting, setDeleting] = useState<Section | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)

  useImperativeHandle(ref, () => ({ focusAdd: () => topAdd.current?.focus() }), [])

  // #i-<item>: focus that item once (a search result, a citation).
  const focused = useRef<string | null>(null)
  useEffect(() => {
    if (!focusItem || focused.current === focusItem) return
    const el = inputs.current.get(focusItem)
    if (el) {
      focused.current = focusItem
      el.focus()
      el.closest('.nt-row')?.classList.add('flash')
      el.scrollIntoView({ block: 'center' })
    } else if (items.some((i) => i.id === focusItem && i.checked)) setFoldOpen(true)
  }, [focusItem, items])

  const focusAt = (id: string | undefined, end = true) => {
    if (!id) return
    requestAnimationFrame(() => {
      const el = inputs.current.get(id)
      if (!el) return
      el.focus()
      if (end) el.setSelectionRange(el.value.length, el.value.length)
    })
  }
  const neighbour = (id: string, d: 1 | -1) => order[order.indexOf(id) + d]

  // ACT checkitem.check @web.note, ACT checkitem.uncheck @web.note
  const toggle = (item: Item) => {
    if (readOnly) return
    if (!item.checked && note.checked_at_bottom) {
      setLingering((s) => new Set(s).add(item.id))
      void patchItem(qc, note.id, item, { checked: true })
      window.setTimeout(() => setLingering((s) => {
        const n = new Set(s)
        n.delete(item.id)
        return n
      }), CHECK_LINGER_MS)
    } else void patchItem(qc, note.id, item, { checked: !item.checked })
  }

  const sectionNames = sections.map((s) => s.name)
  const groupCount = (g: Group) => g.items.filter((i) => !i.checked).length

  const rowProps = (item: Item, group: Group) => ({
    item, note, sections, readOnly,
    register: (el: HTMLInputElement | null) => (el ? inputs.current.set(item.id, el) : inputs.current.delete(item.id)),
    onToggle: () => toggle(item),
    onEnter: () => setInsertAfter(item.id),
    onPrev: () => focusAt(neighbour(item.id, -1)),
    onNext: () => focusAt(neighbour(item.id, 1)),
    onDeleted: () => focusAt(neighbour(item.id, -1)),
    onNewSection: () => setNewSectionAfter({ after: group.section?.id ?? null }),
    onMoved: () => focusAt(item.id),
  })

  const groupBlock = (g: Group, idx: number) => {
    const sid = g.section?.id ?? null
    const collapsed = !!g.section?.collapsed
    return (
      <div key={sid ?? 'top'} className="nt-group" data-section={g.section?.name ?? ''}>
        {g.section && (
          <SectionHeader note={note} section={g.section} index={idx - 1} total={sections.length} count={groupCount(g)} readOnly={readOnly}
            onDelete={() => setDeleting(g.section)} />
        )}
        {!collapsed && (
          <>
            {sid === null && !readOnly && (
              <>
                <AddLine ref={topAdd} note={note} sectionId={null} sectionNames={sectionNames} placeholder={tr('notes.checklist.addItem')} />
                {note.suggest_usual && <UsualStrip note={note} />}
              </>
            )}
            <ul className="nt-rows" onDragOver={(e) => e.dataTransfer.types.includes('application/x-mailr-item') && e.preventDefault()}
              onDrop={(e) => {
                // A drop on the group's empty space: the end of this group.
                const id = e.dataTransfer.getData('application/x-mailr-item')
                if (!id || (e.target as HTMLElement).closest('.nt-row')) return
                e.preventDefault()
                const last = g.items.filter((i) => i.id !== id).at(-1)
                void moveItem(qc, note.id, id, last ? { after: last.id } : { section_id: sid })
              }}>
              {g.items.map((item) => (
                <ItemRowWithInsert key={item.id} rowProps={rowProps(item, g)} insert={insertAfter === item.id}
                  onInsertDone={() => setInsertAfter(null)} note={note} sectionId={sid} sectionNames={sectionNames} />
              ))}
            </ul>
            {sid !== null && !readOnly && <AddLine note={note} sectionId={sid} sectionNames={sectionNames} placeholder={tr('notes.checklist.addItem')} compact />}
          </>
        )}
      </div>
    )
  }

  const foldBySection = useMemo(() => {
    const out: { name: string | null; items: Item[] }[] = []
    for (const i of view.checked) {
      const name = sections.find((s) => s.id === i.section_id)?.name ?? null
      const g = out.find((x) => x.name === name)
      if (g) g.items.push(i)
      else out.push({ name, items: [i] })
    }
    return out
  }, [view.checked, sections])

  return (
    <div className="nt-checklist" data-testid="checklist">
      {view.groups.map(groupBlock)}
      {!readOnly && (
        <button type="button" className="btn text sm nt-add-section" onClick={() => setNewSectionAfter({ after: sections.at(-1)?.id ?? null })}>
          <Plus size={16} />{' '}{tr('common.addSection')}
        </button>
      )}
      {note.checked_at_bottom && view.checked.length > 0 && (
        <div className="nt-fold" data-testid="checked-fold">
          <div className="nt-fold-head">
            <button type="button" className="nt-fold-toggle" aria-expanded={foldOpen} onClick={() => setFoldOpen((v) => !v)}>
              {foldOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              {tr('notes.checklist.checked', { checkedLength: view.checked.length })}
            </button>
            {/* ACT note.clear_checked @web.note */}
            {!readOnly && <button type="button" className="btn text sm" onClick={() => setConfirmClear(true)}>{tr('notes.checklist.clearChecked')}</button>}
          </div>
          {foldOpen && foldBySection.map((g) => (
            <div key={g.name ?? 'top'} className="nt-fold-group">
              {g.name && <div className="nt-fold-section">{g.name}</div>}
              <ul className="nt-rows">
                {g.items.map((item) => <ItemRow key={item.id} {...rowProps(item, { section: null, mode: 'manual', items: [] })} />)}
              </ul>
            </div>
          ))}
        </div>
      )}
      {newSectionAfter && (
        <NameDialog title={tr('notes.checklist.newSection')} label={tr('notes.checklist.sectionName')} initial="" onClose={() => setNewSectionAfter(null)} onSave={(name) => {
          // ACT checksection.create @web.note
          const after = newSectionAfter.after
          setNewSectionAfter(null)
          // Items typed as "Costco: milk" before the section existed: the one toast offers to
          // move them in, beside its Undo.
          const found = prefixedItems(items, name)
          void createSection(qc, note.id, name, after, { quiet: found.length > 0 }).then((r) => {
            const sec = r.section
            if (!found.length || !sec) return
            const n = tr('notes.checklist.itemsStart', { count: found.length })
            toast({
              message: tr('notes.checklist.sectionAddedWith', { secName: sec.name, n }),
              actionLabel: tr('notes.checklist.moveThemIn', { count: found.length }), duration: 10_000,
              // ACT checkitem.move @web.note
              onAction: () => void adoptPrefixed(qc, note.id, sec, found),
              ...(r.undo ? { secondaryLabel: tr('common.undo'), onSecondary: () => void runUndo(qc, r.undo!) } : {}),
            })
          }).catch(() => undefined)
        }} />
      )}
      {deleting && <DeleteSectionDialog section={deleting} count={items.filter((i) => i.section_id === deleting.id).length}
        onClose={() => setDeleting(null)}
        onDelete={(mode) => {
          // ACT checksection.delete @web.note
          void deleteSection(qc, note.id, deleting.id, mode)
          setDeleting(null)
        }} />}
      <ConfirmDialog open={confirmClear} title={tr('notes.checklist.clearCheckedItems')} body={tr('notes.checklist.checkedItemsGoToThe', { count: view.checked.length })}
        confirmLabel={tr('notes.checklist.clear')} focusConfirm onClose={() => setConfirmClear(false)} onConfirm={() => {
          setConfirmClear(false)
          void clearChecked(qc, note.id)
        }} />
    </div>
  )
})

function ItemRowWithInsert({ rowProps, insert, onInsertDone, note, sectionId, sectionNames }: {
  rowProps: RowProps; insert: boolean; onInsertDone: () => void; note: Note; sectionId: string | null; sectionNames: string[]
}) {
  return (
    <>
      <ItemRow {...rowProps} />
      {insert && (
        <li className="nt-insert">
          <AddLine note={note} sectionId={sectionId} sectionNames={sectionNames} after={rowProps.item.id} autoFocus onDone={onInsertDone} placeholder={tr('notes.checklist.newItem')} compact />
        </li>
      )}
    </>
  )
}

// ---------------------------------------------------------------- one item

interface RowProps {
  item: Item
  note: Note
  sections: Section[]
  readOnly: boolean
  register: (el: HTMLInputElement | null) => void
  onToggle: () => void
  onEnter: () => void
  onPrev: () => void
  onNext: () => void
  onDeleted: () => void
  onNewSection: () => void
  onMoved: () => void
}

function ItemRow({ item, note, sections, readOnly, register, onToggle, onEnter, onPrev, onNext, onDeleted, onNewSection, onMoved }: RowProps) {
  const qc = useQueryClient()
  const me = useMe().data?.user.id
  const nav = useNavigate()
  const [text, setText] = useState(item.text)
  const [editing, setEditing] = useState(false)
  const [extra, setExtra] = useState<string | null>(null)
  const [drop, setDrop] = useState<'before' | 'after' | null>(null)
  const qtyA = useAnchor<HTMLButtonElement>()
  const menuA = useAnchor<HTMLButtonElement>()
  const extraRef = useRef<HTMLInputElement>(null)
  // The item's text from the server shows unless this row holds typing not yet saved (an undo in
  // the editor's history changes a row that has the focus, too).
  const shown = useRef(item.text)
  useEffect(() => {
    if (!editing || text === shown.current) setText(item.text)
    shown.current = item.text
  }, [item.text, editing]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (extra !== null) extraRef.current?.focus()
  }, [extra])

  const commit = () => {
    setEditing(false)
    const t = text.replace(/\s+/g, ' ').trim()
    if (t && t !== item.text) {
      // ACT checkitem.edit @web.note: the grammar in edit mode (a typed x2 becomes the quantity chip).
      void patchItem(qc, note.id, item, { text: t, parse: true }, { label: tr('notes.checklist.itemChanged') })
    } else setText(item.text)
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const mod = e.ctrlKey || e.metaKey
    if (e.key === 'Enter' && mod) {
      e.preventDefault()
      onToggle()
    } else if (e.key === 'Enter' && e.altKey) {
      e.preventDefault()
      commit()
      onNewSection()
    } else if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault()
      setExtra(item.extra ?? '')
    } else if (e.key === 'Enter') {
      e.preventDefault()
      commit()
      onEnter()
    } else if (e.key === 'Backspace' && !text && !e.repeat) {
      e.preventDefault()
      setEditing(false)
      // ACT checkitem.delete @web.note
      void deleteItem(qc, note.id, item)
      onDeleted()
    } else if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault()
      commit()
      const up = e.key === 'ArrowUp'
      // ACT checkitem.move @web.note
      void moveItem(qc, note.id, item.id, { direction: e.shiftKey ? (up ? 'prev_section' : 'next_section') : up ? 'up' : 'down' }).then(onMoved)
    } else if (e.key === 'ArrowUp' && !e.altKey) {
      e.preventDefault()
      commit()
      onPrev()
    } else if (e.key === 'ArrowDown' && !e.altKey) {
      e.preventDefault()
      commit()
      onNext()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      setText(item.text)
      setEditing(false)
      e.currentTarget.blur()
    }
  }

  const onDragOver = (e: DragEvent) => {
    if (readOnly || !e.dataTransfer.types.includes('application/x-mailr-item')) return
    e.preventDefault()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setDrop(e.clientY < r.top + r.height / 2 ? 'before' : 'after')
  }

  const q = qtyLabel(item.qty)
  const author = authorLine(item, me)
  // A change made offline that waits for the connection: a dashed edge (offline.ts).
  const pending = useNotesQueue((s) => s.ops.some((o) => o.note === note.id && (o.item === item.id || !!o.items?.some((i) => i.id === item.id))))
  // Another person's tick or change just arrived: their name beside the row for a moment.
  const touchedBy = useTouchedBy(item.id)
  return (
    <li id={`i-${item.id}`} className={`nt-row${item.checked ? ' checked' : ''}${drop ? ` drop-${drop}` : ''}${pending ? ' pending' : ''}`} data-item={item.id}
      data-pending={pending || undefined} title={pending ? tr('notes.checklist.waitsToBeSent') : undefined}
      onFocusCapture={() => useNotesUI.getState().set({ focusedItem: { note: note.id, item: item.id } })}
      onDragOver={onDragOver} onDragLeave={() => setDrop(null)}
      onDrop={(e) => {
        const id = e.dataTransfer.getData('application/x-mailr-item')
        const where = drop
        setDrop(null)
        if (!id || id === item.id || !where) return
        e.preventDefault()
        e.stopPropagation()
        void moveItem(qc, note.id, id, where === 'before' ? { before: item.id } : { after: item.id })
      }}>
      {!readOnly && (
        <span className="nt-grip" draggable aria-hidden onDragStart={(e) => {
          e.dataTransfer.setData('application/x-mailr-item', item.id)
          e.dataTransfer.effectAllowed = 'move'
        }}><GripVertical size={16} /></span>
      )}
      <input type="checkbox" className="checkbox hit24" checked={item.checked} disabled={readOnly} aria-label={`${item.checked ? tr('notes.checklist.uncheck') : tr('notes.checklist.check')} ${item.text}`} onChange={onToggle} />
      {q && (
        <button type="button" className="nt-qty" disabled={readOnly} aria-label={tr('notes.checklist.quantity', { q })} data-tip={tr('notes.checklist.changeTheQuantity')} onClick={(e) => qtyA.toggle(e.currentTarget)}>{q}</button>
      )}
      <div className="nt-row-main">
        <input ref={register} className="nt-item-input" dir="auto" value={text} readOnly={readOnly} aria-label={tr('notes.checklist.item')} maxLength={1000}
          data-history={text === item.text ? 'item' : undefined}
          onFocus={() => setEditing(true)} onBlur={commit} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} />
        {extra !== null ? (
          <input ref={extraRef} className="nt-extra-input" dir="auto" value={extra} placeholder={tr('notes.checklist.aNoteOnThisItem')} aria-label={tr('notes.checklist.extraLine')} maxLength={500}
            onChange={(e) => setExtra(e.target.value)}
            onBlur={() => {
              const v = extra.trim() || null
              setExtra(null)
              if (v !== (item.extra ?? null)) void patchItem(qc, note.id, item, { extra: v }, { label: tr('notes.checklist.itemChanged') })
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === 'Escape') {
                e.preventDefault()
                e.stopPropagation()
                ;(e.target as HTMLInputElement).blur()
              }
            }} />
        ) : item.extra ? <div className="nt-extra" dir="auto" onClick={() => !readOnly && setExtra(item.extra ?? '')}>{item.extra}</div> : null}
        {/* Sharing: an item shows who wrote it when that is not the viewer. */}
        {author && <div className="nt-item-author" data-testid="item-author">{author}</div>}
      </div>
      {touchedBy && <span className="nt-touched" data-testid="item-touched" aria-live="polite">{touchedBy}</span>}
      {item.task_uid && (
        <button type="button" className="nt-task-chip" data-tip={tr('notes.checklist.openTheTask')} onClick={() => nav(taskPath(item.task_uid!))}><CircleCheck size={14} />{' '}{tr('common.task')}</button>
      )}
      {!readOnly && (
        <button type="button" className="icon-btn sm nt-row-menu" aria-label={tr('notes.checklist.moreFor', { text: item.text })} data-tip={tr('common.more')} onClick={(e) => menuA.toggle(e.currentTarget)}><MoreVertical size={16} /></button>
      )}
      <Popover anchor={qtyA.anchor} open={qtyA.open} onClose={qtyA.close} role="dialog" label={tr('notes.checklist.quantity2')} placement="bottom-start">
        <QtyEditor item={item} onSave={(qty) => {
          qtyA.close()
          void patchItem(qc, note.id, item, { qty }, { label: tr('notes.checklist.quantityChanged') })
        }} />
      </Popover>
      <Popover anchor={menuA.anchor} open={menuA.open} onClose={menuA.close} placement="bottom-end" label={tr('notes.checklist.item')}>
        {!item.qty && <MenuItem icon={<Plus size={16} />} onSelect={() => {
          menuA.close()
          void patchItem(qc, note.id, item, { qty: { amount: '2', unit: null } }, { label: tr('notes.checklist.quantityChanged') })
        }}>{tr('notes.checklist.addAQuantity')}</MenuItem>}
        <MenuItem onSelect={() => {
          menuA.close()
          setExtra(item.extra ?? '')
        }}>{item.extra ? tr('notes.checklist.editTheExtraLine') : tr('notes.checklist.addAnExtraLine')}</MenuItem>
        {/* ACT checkitem.to_task @web.note */}
        {!item.task_uid && <MenuItem icon={<ListTodo size={16} />} onSelect={() => {
          menuA.close()
          void itemToTask(qc, note.id, item.id)
        }}>{tr('notes.checklist.turnIntoATask')}</MenuItem>}
        <SubMenu label={tr('notes.checklist.moveToSection')}>
          <MenuItem selected={!item.section_id} onSelect={() => {
            menuA.close()
            void moveItem(qc, note.id, item.id, { section_id: null })
          }}>{tr('notes.checklist.noSection')}</MenuItem>
          {sections.map((s) => (
            <MenuItem key={s.id} selected={item.section_id === s.id} onSelect={() => {
              menuA.close()
              void moveItem(qc, note.id, item.id, { section_id: s.id })
            }}>{s.name}</MenuItem>
          ))}
        </SubMenu>
        <MenuItem shortcut="Alt+↑" onSelect={() => {
          menuA.close()
          void moveItem(qc, note.id, item.id, { direction: 'up' })
        }}>{tr('common.moveUp')}</MenuItem>
        <MenuItem shortcut="Alt+↓" onSelect={() => {
          menuA.close()
          void moveItem(qc, note.id, item.id, { direction: 'down' })
        }}>{tr('common.moveDown')}</MenuItem>
        <MenuSep />
        <MenuItem icon={<Trash2 size={16} />} danger onSelect={() => {
          menuA.close()
          void deleteItem(qc, note.id, item)
        }}>{tr('common.delete2')}</MenuItem>
      </Popover>
    </li>
  )
}

/** − and + for a count, a field for the amount and the unit, and Remove. */
function QtyEditor({ item, onSave }: { item: Item; onSave: (q: Item['qty']) => void }) {
  const [amount, setAmount] = useState(item.qty?.amount ?? '1')
  const [unit, setUnit] = useState(item.qty?.unit ?? '')
  const save = () => {
    const a = amount.trim()
    if (!a || !/^\d+(\.\d+)?$/.test(a) || Number(a) <= 0) return onSave(null)
    onSave({ amount: String(Number(a)), unit: unit.trim() || null })
  }
  return (
    <div className="nt-qty-editor" onKeyDown={(e) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        save()
      }
    }}>
      <div className="nt-qty-step">
        <button type="button" className="icon-btn sm" aria-label={tr('notes.checklist.oneLess')} onClick={() => onSave(stepQty(item.qty, -1))}><Minus size={16} /></button>
        <input value={amount} aria-label={tr('common.amount')} inputMode="decimal" size={5} onChange={(e) => setAmount(e.target.value)} data-autofocus />
        <button type="button" className="icon-btn sm" aria-label={tr('notes.checklist.oneMore')} onClick={() => onSave(stepQty(item.qty, 1))}><Plus size={16} /></button>
      </div>
      <input value={unit} aria-label={tr('common.unit')} placeholder={tr('notes.checklist.unitGKgPack')} size={10} onChange={(e) => setUnit(e.target.value)} />
      <div className="nt-qty-actions">
        <button type="button" className="btn text sm" onClick={() => onSave(null)}>{tr('common.remove')}</button>
        <button type="button" className="btn filled sm" onClick={save}>{tr('common.save')}</button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- sections

function SectionHeader({ note, section, index, total, count, readOnly, onDelete }: {
  note: Note; section: Section; index: number; total: number; count: number; readOnly: boolean; onDelete: () => void
}) {
  const qc = useQueryClient()
  const menu = useAnchor<HTMLButtonElement>()
  const [renaming, setRenaming] = useState(false)
  const [drop, setDrop] = useState(false)
  const learned = section.order_mode === 'learned'
  // ACT checksection.reorder @web.note
  const move = (direction: 'up' | 'down') => void patchSection(qc, note.id, section.id, { direction }, tr('notes.checklist.sectionMoved'))
  return (
    <div className={`nt-section-head${drop ? ' drop' : ''}`} tabIndex={0} data-section-id={section.id}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget || readOnly) return
        if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
          e.preventDefault()
          if (e.key === 'ArrowUp' && index > 0) move('up')
          if (e.key === 'ArrowDown' && index < total - 1) move('down')
        } else if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          void patchSection(qc, note.id, section.id, { collapsed: !section.collapsed })
        }
      }}
      onDragOver={(e) => {
        if (!readOnly && e.dataTransfer.types.includes('application/x-mailr-item')) {
          e.preventDefault()
          setDrop(true)
        }
      }}
      onDragLeave={() => setDrop(false)}
      onDrop={(e) => {
        setDrop(false)
        const id = e.dataTransfer.getData('application/x-mailr-item')
        if (!id) return
        e.preventDefault()
        void moveItem(qc, note.id, id, { section_id: section.id })
      }}>
      <button type="button" className="nt-section-toggle" aria-expanded={!section.collapsed} onClick={() => void patchSection(qc, note.id, section.id, { collapsed: !section.collapsed })}>
        {section.collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        <span className="nt-section-name" dir="auto">{section.name}</span>
        {section.collapsed && <span className="nt-section-count">({count})</span>}
      </button>
      <span className="spacer" />
      <span className="nt-section-mode muted">{learned ? 'learned' : ''}</span>
      {!readOnly && <button type="button" className="icon-btn sm" aria-label={tr('notes.checklist.sectionMenu', { sectionName: section.name })} data-tip={tr('notes.checklist.sectionMenu2')} onClick={(e) => menu.toggle(e.currentTarget)}><MoreVertical size={16} /></button>}
      <Popover anchor={menu.anchor} open={menu.open} onClose={menu.close} placement="bottom-end" label={tr('notes.checklist.section')}>
        <MenuItem onSelect={() => {
          menu.close()
          setRenaming(true)
        }}>{tr('common.rename')}</MenuItem>
        <MenuHeader>{tr('common.order')}</MenuHeader>
        {/* ACT checksection.order_mode @web.note */}
        <MenuItem selected={learned} onSelect={() => {
          menu.close()
          void patchSection(qc, note.id, section.id, { order_mode: 'learned' }, tr('notes.checklist.learnedOrder'))
        }}>{tr('notes.checklist.learned', { value: section.trips ? tr('notes.checklist.fromTrips', { count: section.trips }) : '' })}</MenuItem>
        <MenuItem selected={!learned} onSelect={() => {
          menu.close()
          void patchSection(qc, note.id, section.id, { order_mode: 'manual' }, tr('common.manualOrder'))
        }}>{tr('common.manual')}</MenuItem>
        {learned && <MenuItem onSelect={() => {
          menu.close()
          void patchSection(qc, note.id, section.id, { reset_learned: true }, tr('notes.checklist.learnedOrderReset'))
        }}>{tr('notes.checklist.resetLearnedOrder')}</MenuItem>}
        <MenuSep />
        <MenuItem disabled={index <= 0} shortcut="Alt+↑" onSelect={() => {
          menu.close()
          move('up')
        }}>{tr('common.moveUp')}</MenuItem>
        <MenuItem disabled={index >= total - 1} shortcut="Alt+↓" onSelect={() => {
          menu.close()
          move('down')
        }}>{tr('common.moveDown')}</MenuItem>
        <MenuSep />
        <MenuItem icon={<Trash2 size={16} />} danger onSelect={() => {
          menu.close()
          onDelete()
        }}>{tr('common.delete2')}</MenuItem>
      </Popover>
      {renaming && (
        <NameDialog title={tr('notes.checklist.renameSection')} label={tr('notes.checklist.sectionName')} initial={section.name} onClose={() => setRenaming(false)} onSave={(name) => {
          setRenaming(false)
          // ACT checksection.rename @web.note
          if (name !== section.name) void patchSection(qc, note.id, section.id, { name, base_name: section.name }, tr('notes.checklist.sectionRenamed'))
        }} />
      )}
    </div>
  )
}

function DeleteSectionDialog({ section, count, onClose, onDelete }: { section: Section; count: number; onClose: () => void; onDelete: (m: 'keep' | 'delete') => void }) {
  const [mode, setMode] = useState<'keep' | 'delete'>('keep')
  return (
    <Dialog open onClose={onClose} title={tr('notes.checklist.deleteTheSection', { sectionName: section.name })} footer={
      <>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled danger" onClick={() => onDelete(mode)}>{tr('notes.checklist.deleteSection')}</button>
      </>
    }>
      {count > 0 ? (
        <div className="nt-radio-list" role="radiogroup" aria-label={tr('notes.checklist.itsItems')}>
          <label><input type="radio" name="nt-del-sec" checked={mode === 'keep'} onChange={() => setMode('keep')} />{' '}{tr('notes.checklist.keepTheItemsTheyMove', { count })}</label>
          <label><input type="radio" name="nt-del-sec" checked={mode === 'delete'} onChange={() => setMode('delete')} />{' '}{tr('notes.checklist.deleteThemToo')}</label>
        </div>
      ) : <p>{tr('notes.checklist.theSectionHasNoItems')}</p>}
    </Dialog>
  )
}

export function NameDialog({ title, label, initial, onClose, onSave }: { title: string; label: string; initial: string; onClose: () => void; onSave: (name: string) => void }) {
  const [v, setV] = useState(initial)
  const ok = v.trim().length > 0
  return (
    <Dialog open onClose={onClose} title={title} initialFocus="input" footer={
      <>
        <button type="button" className="btn text" onClick={onClose}>{tr('common.cancel')}</button>
        <button type="button" className="btn filled" disabled={!ok} onClick={() => onSave(v.trim())}>{tr('common.save')}</button>
      </>
    }>
      <label className="field">
        <span>{label}</span>
        <input value={v} maxLength={100} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter' && ok) {
            e.preventDefault()
            onSave(v.trim())
          }
        }} />
      </label>
    </Dialog>
  )
}

// ---------------------------------------------------------------- the add line

interface AddProps {
  note: Note
  sectionId: string | null
  sectionNames: string[]
  placeholder: string
  after?: string
  autoFocus?: boolean
  compact?: boolean
  onDone?: () => void
}

/** One add line: the item grammar in list mode, its chips for a single line, a preview for a paste. */
// ACT checkitem.add @web.note, ACT note.add_items @web.note
export const AddLine = forwardRef<HTMLInputElement, AddProps>(function AddLine({ note, sectionId, sectionNames, placeholder, after, autoFocus, compact, onDone }, ref) {
  const qc = useQueryClient()
  const [value, setValue] = useState('')
  const [keep, setKeep] = useState<string[]>([])
  const [paste, setPaste] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const afterRef = useRef(after)
  const inner = useRef<HTMLInputElement>(null)
  useImperativeHandle(ref, () => inner.current!, [])
  useEffect(() => {
    if (autoFocus) inner.current?.focus()
  }, [autoFocus])

  const list = { id: note.id, name: note.title || tr('notes.checklist.thisList'), sections: sectionNames }
  const parsed = useMemo(() => (value.trim() ? parseItems(value, { mode: 'list', list, keep }) : null), [value, keep]) // eslint-disable-line react-hooks/exhaustive-deps
  const pasted = useMemo(() => (paste ? parseItems(paste, { mode: 'list', list }) : null), [paste]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (lines: string, k: string[]) => {
    if (!lines.trim() || busy) return
    setBusy(true)
    try {
      const res = await addItems(qc, note.id, { lines, section_id: sectionId, after: afterRef.current ?? null, keep: k.length ? k : undefined })
      const last = res.items?.at(-1)?.id
      if (afterRef.current && last) afterRef.current = last
      setValue('')
      setKeep([])
    } finally {
      setBusy(false)
    }
  }

  const chips: ItemToken[] = parsed && !value.includes('\n') ? parsed.tokens.filter((t) => t.kind === 'section' || t.kind === 'qty' || t.kind === 'check') : []
  const summary = pasted ? pasteSummary(pasted.items.map((i) => i.section ?? null), note.title || tr('notes.checklist.thisList')) : ''

  return (
    <div className={`nt-addline${compact ? ' compact' : ''}`}>
      <Plus size={16} aria-hidden className="nt-addline-icon" />
      <input ref={inner} value={value} placeholder={placeholder} aria-label={placeholder} maxLength={20000} disabled={busy && !value}
        data-history={value ? undefined : 'add'}
        onChange={(e) => setValue(e.target.value)}
        onPaste={(e) => {
          const text = e.clipboardData.getData('text/plain')
          if (!text.includes('\n')) return
          e.preventDefault()
          setPaste(text)
        }}
        onBlur={() => {
          if (!value && onDone) onDone()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
            e.preventDefault()
            if (paste) {
              const p = paste
              setPaste(null)
              void send(p, [])
            } else if (value.trim()) void send(value, keep)
            else onDone?.()
          } else if (e.key === 'Escape') {
            // Esc leaves the line and keeps its text.
            e.preventDefault()
            e.stopPropagation()
            if (paste) setPaste(null)
            else {
              inner.current?.blur()
              onDone?.()
            }
          } else if (e.key === 'Backspace' && !value && keep.length) {
            setKeep([])
          }
        }} />
      {chips.length > 0 && (
        <div className="nt-chips" aria-label={tr('notes.checklist.whatTheLineAdds')}>
          {chips.map((t) => (
            <span key={`${t.kind}-${t.start}`} className={`nt-chip nt-chip-${t.kind}`}>
              {chipLabel(t, parsed?.items ?? [])}
              <button type="button" aria-label={tr('notes.checklist.keepAsText', { text: t.text })} data-tip={tr('notes.checklist.keepAsText2')} onMouseDown={(e) => e.preventDefault()} onClick={() => setKeep((k) => [...k, t.text])}><X size={12} /></button>
            </span>
          ))}
        </div>
      )}
      {pasted && (
        <div className="nt-paste" role="dialog" aria-label={tr('notes.checklist.addThePastedItems')}>
          <p>{summary}</p>
          <ul>{pasted.items.slice(0, 12).map((i, n) => <li key={n}>{i.checked ? '☑' : '☐'} {i.qty ? `${qtyLabel({ ...i.qty })} ` : ''}{i.text}{i.section ? <span className="muted"> · {i.section}</span> : null}</li>)}</ul>
          {pasted.items.length > 12 && <p className="muted">{tr('notes.checklist.andMore', { value: pasted.items.length - 12 })}</p>}
          <div className="nt-paste-actions">
            <button type="button" className="btn text sm" onClick={() => setPaste(null)}>{tr('common.cancel')}</button>
            <button type="button" className="btn filled sm" onClick={() => {
              const p = paste!
              setPaste(null)
              void send(p, [])
            }}>{tr('common.add')}</button>
          </div>
        </div>
      )}
    </div>
  )
})

function chipLabel(t: ItemToken, items: { qty: { amount: string; unit: string | null; text?: string } | null; section: string | null }[]): ReactNode {
  if (t.kind === 'section') return <>{tr('notes.checklist.section2', { section: items[0]?.section ?? t.text.replace(/:$/, '').replace(/^#+\s*/, '') })}</>
  if (t.kind === 'check') return tr('common.checked')
  const q = items.find((i) => i.qty?.text === t.text)?.qty
  return <>{tr('notes.checklist.quantity3', { value: q ? (q.unit ? `${q.amount} ${q.unit}` : q.amount) : t.text })}</>
}

/** "Add 7 items to Shopping (Costco 3, Corner market 4)?" */
export function pasteSummary(sections: (string | null)[], listName: string): string {
  const n = sections.length
  const counts = new Map<string, number>()
  for (const s of sections) if (s) counts.set(s, (counts.get(s) ?? 0) + 1)
  const per = [...counts.entries()].map(([s, c]) => `${s} ${c}`).join(', ')
  return tr('notes.checklist.addItemsTo', { count: n, listName, value: per ? ` (${per})` : '' })
}

// ---------------------------------------------------------------- usual items

/** "Usual: eggs · bananas · coffee": a tap adds the item to its last section with its last quantity. */
function UsualStrip({ note }: { note: Note }) {
  const qc = useQueryClient()
  const usual = useUsual(note.id, note.suggest_usual)
  const [menu, setMenu] = useState<{ key: string; anchor: HTMLElement } | null>(null)
  const items = usual.data?.items ?? []
  if (!items.length) return null
  return (
    <div className="nt-usual" data-testid="usual-strip" aria-label={tr('notes.checklist.usualItems')}>
      <span className="nt-usual-label">{tr('notes.checklist.usual')}</span>
      {items.map((u) => (
        <span key={u.key} className="nt-usual-chip" onContextMenu={(e) => {
          e.preventDefault()
          setMenu({ key: u.key, anchor: e.currentTarget })
        }}>
          {/* ACT checkitem.usual_add @web.note */}
          <button type="button" className="nt-usual-add" data-tip={u.section ? tr('notes.checklist.addsTo', { section: u.section }) : tr('notes.checklist.addsToTheTopOf')} onClick={() => void addUsual(qc, note.id, [u.key])}>
            {qtyLabel(u.qty) ? `${qtyLabel(u.qty)} ` : ''}{u.text}
          </button>
          {/* ACT checkitem.usual_dismiss @web.note */}
          <button type="button" className="nt-usual-x" aria-label={tr('notes.checklist.hideForNow', { text: u.text })} data-tip={tr('notes.checklist.hideForNowRightClick')} onClick={() => void dismissUsual(qc, note.id, u.key, 'interval')}><X size={12} /></button>
        </span>
      ))}
      {items.length > 1 && <button type="button" className="btn text sm" onClick={() => void addUsual(qc, note.id, items.map((u) => u.key))}>{tr('notes.checklist.addAll')}</button>}
      <Popover anchor={menu?.anchor ?? null} open={!!menu} onClose={() => setMenu(null)} placement="bottom-start" label={tr('notes.checklist.suggestion')}>
        <MenuItem onSelect={() => {
          const k = menu!.key
          setMenu(null)
          void dismissUsual(qc, note.id, k, 'interval')
        }}>{tr('notes.checklist.dismissForNow')}</MenuItem>
        <MenuItem onSelect={() => {
          const k = menu!.key
          setMenu(null)
          void dismissUsual(qc, note.id, k, 'never')
        }}>{tr('notes.checklist.neverSuggest')}</MenuItem>
      </Popover>
    </div>
  )
}

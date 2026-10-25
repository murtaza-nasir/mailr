// The left column: smart lists with live counts (Today's overdue count in
// red), Waiting for reply, My lists (colour, count, a menu, drag to reorder, "+ New list") and
// Tags (colour, count, a menu). Every entry is also a drop target for dragged tasks.

import {
  AlarmClock, CalendarClock, CalendarDays, CalendarOff, CheckCheck, Flag, ListTodo, Lock, Mail, MoreVertical, Plus, Sun, Tag,
} from 'lucide-react'
import { useState, type DragEvent, type ReactNode } from 'react'
import { NavLink } from 'react-router'
import { MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { tasksPath, type SmartList, type TaskListRef } from '../../lib/routes'
import { autoColor, SWATCHES } from './colors'
import type { Counts, TagInfo, TaskList } from './model'
import { listKey } from './model'
import { currentDrag, DRAG_TYPE } from './TaskList'
import { t as tr } from '../../i18n'

export const SMART_META: Record<SmartList, { label: string; icon: ReactNode }> = {
  today: { label: tr('common.today'), icon: <Sun /> },
  upcoming: { label: tr('common.upcoming'), icon: <CalendarDays /> },
  scheduled: { label: tr('common.scheduled'), icon: <CalendarClock /> },
  nodate: { label: tr('common.noDate'), icon: <CalendarOff /> },
  high: { label: tr('common.highPriority'), icon: <Flag /> },
  email: { label: tr('tasks.sidebar.fromEmail'), icon: <Mail /> },
  waiting: { label: tr('tasks.sidebar.waitingForReply'), icon: <AlarmClock /> },
  all: { label: tr('common.all'), icon: <ListTodo /> },
  completed: { label: tr('common.completed'), icon: <CheckCheck /> },
}

const ORDER: SmartList[] = ['today', 'upcoming', 'scheduled', 'nodate', 'high', 'email', 'waiting', 'all', 'completed']

export interface SidebarDrop {
  /** Dropped tasks on a list (task.move_list), a tag (task.tag) or a smart list (task.reschedule). */
  onDropList: (uids: string[], list: TaskList) => void
  onDropTag: (uids: string[], tag: string) => void
  onDropSmart: (uids: string[], id: SmartList) => void
}

interface Props extends SidebarDrop {
  current: TaskListRef | null
  counts: Counts | null
  waitingCount: number
  lists: TaskList[]
  tags: TagInfo[]
  tagColor: (name: string) => string | null
  defaultList: string | null
  onNewList: () => void
  onListMenu: (l: TaskList, action: 'rename' | 'color' | 'default' | 'delete', color?: string | null) => void
  onReorderList: (l: TaskList, before: TaskList | null) => void
  onTagMenu: (t: TagInfo, action: 'rename' | 'color' | 'delete', color?: string | null) => void
  onNavigate?: () => void
}

function dropProps(accept: boolean, onDrop: (uids: string[]) => void, setOver: (b: boolean) => void) {
  return {
    onDragOver: (e: DragEvent) => {
      if (!accept || !currentDrag().length) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setOver(true)
    },
    onDragLeave: () => setOver(false),
    onDrop: (e: DragEvent) => {
      setOver(false)
      if (!accept) return
      e.preventDefault()
      let uids = currentDrag()
      if (!uids.length) {
        try {
          uids = JSON.parse(e.dataTransfer.getData(DRAG_TYPE) || '[]') as string[]
        } catch {
          uids = []
        }
      }
      if (uids.length) onDrop(uids)
    },
  }
}

function Entry({ to, icon, label, count, danger, active, accept, onDrop, menu, testid, onNavigate, dragProps }: {
  to: string; icon: ReactNode; label: string; count?: number | null; danger?: number | null; active: boolean; accept: boolean
  onDrop: (uids: string[]) => void; menu?: ReactNode; testid?: string; onNavigate?: () => void; dragProps?: Record<string, unknown>
}) {
  const [over, setOver] = useState(false)
  return (
    <div className={`tk-nav-item${active ? ' active' : ''}${over ? ' drop' : ''}`} {...dropProps(accept, onDrop, setOver)} {...dragProps}>
      <NavLink to={to} className="tk-nav-link" data-testid={testid} aria-current={active ? 'page' : undefined} onClick={onNavigate}>
        <span className="tk-nav-icon" aria-hidden>{icon}</span>
        <span className="tk-nav-label ellipsis">{label}</span>
        {danger ? <span className="tk-nav-danger" aria-label={tr('tasks.sidebar.overdue', { danger })}>{danger}</span> : null}
        {count ? <span className="tk-nav-count">{count}</span> : null}
      </NavLink>
      {menu}
    </div>
  )
}

function ColorRow({ current, onPick, noColor }: { current?: string | null; onPick: (c: string | null) => void; noColor?: boolean }) {
  return (
    <div className="tk-swatches" role="group" aria-label={tr('common.colour')}>
      {SWATCHES.map((s) => (
        <button key={s.hex} type="button" className={`tk-swatch${current?.toLowerCase() === s.hex ? ' on' : ''}`} style={{ background: s.hex }}
          aria-label={s.name} title={s.name} onClick={() => onPick(s.hex)} />
      ))}
      {noColor && <button type="button" className="tk-swatch none" aria-label={tr('common.noColour')} title={tr('common.noColour')} onClick={() => onPick(null)} />}
    </div>
  )
}

function ItemMenu({ label, children }: { label: string; children: (close: () => void) => ReactNode }) {
  const a = useAnchor<HTMLButtonElement>()
  return (
    <>
      <button type="button" className="icon-btn sm tk-nav-menu" aria-label={label} onClick={(e) => a.toggle(e.currentTarget)}>
        <MoreVertical size={15} />
      </button>
      <Popover anchor={a.anchor} open={a.open} onClose={a.close} placement="bottom-end" label={label}>
        {children(a.close)}
      </Popover>
    </>
  )
}

export function Sidebar(p: Props) {
  const cur = p.current
  const isOn = (r: TaskListRef) => !!cur && cur.kind === r.kind && cur.id === r.id
  const [dragList, setDragList] = useState<TaskList | null>(null)
  const [showTags, setShowTags] = useState(true)

  const smartCount = (id: SmartList): number | null => {
    if (id === 'waiting') return p.waitingCount || null
    if (id === 'completed') return null
    const c = p.counts?.smart[id]
    return c ?? null
  }

  return (
    <nav className="tk-side" aria-label={tr('tasks.sidebar.taskLists')} data-testid="tasks-sidebar">
      {/* Plain labelled groups of links (a list role would need listitem children). */}
      <div className="tk-side-group" role="group" aria-label={tr('tasks.sidebar.smartLists')}>
        {ORDER.map((id) => (
          <Entry
            key={id}
            to={tasksPath(id)}
            icon={SMART_META[id].icon}
            label={SMART_META[id].label}
            count={smartCount(id)}
            danger={id === 'today' ? p.counts?.smart.overdue ?? null : null}
            active={isOn({ kind: 'smart', id })}
            accept={id === 'today' || id === 'nodate' || id === 'high' || id === 'upcoming'}
            onDrop={(uids) => p.onDropSmart(uids, id)}
            testid={`tasks-nav-${id}`}
            onNavigate={p.onNavigate}
          />
        ))}
      </div>

      <div className="tk-side-head">
        <span>{tr('tasks.sidebar.myLists')}</span>
        <button type="button" className="icon-btn sm" aria-label={tr('common.newList')} title={tr('common.newList')} onClick={p.onNewList}><Plus size={16} /></button>
      </div>
      <div className="tk-side-group" role="group" aria-label={tr('tasks.sidebar.myLists')}>
        {p.lists.map((l) => {
          const key = listKey(l)
          return (
            <Entry
              key={l.id}
              to={tasksPath({ kind: 'list', id: key })}
              icon={l.read_only ? <Lock /> : <i className="tk-dot lg" style={{ background: l.color || autoColor(l.name) }} />}
              label={l.name + (p.defaultList === l.id ? '' : '')}
              count={p.counts?.lists[l.id] ?? null}
              active={isOn({ kind: 'list', id: key })}
              accept={!l.read_only}
              onDrop={(uids) => p.onDropList(uids, l)}
              testid={`tasks-nav-list-${l.name}`}
              onNavigate={p.onNavigate}
              dragProps={{
                draggable: true,
                // ACT list.reorder @web.tasks
                onDragStart: (e: DragEvent) => {
                  setDragList(l)
                  e.dataTransfer.setData('application/x-mailr-list', l.id)
                },
                onDragEnd: () => setDragList(null),
                onDragOverCapture: (e: DragEvent) => {
                  if (dragList && dragList.id !== l.id) e.preventDefault()
                },
                onDropCapture: (e: DragEvent) => {
                  if (!dragList || dragList.id === l.id) return
                  e.preventDefault()
                  e.stopPropagation()
                  p.onReorderList(dragList, l)
                  setDragList(null)
                },
              }}
              menu={
                <ItemMenu label={tr('tasks.sidebar.actionsForList', { itemName: l.name })}>
                  {(close) => (
                    <>
                      {/* ACT list.rename @web.tasks */}
                      <MenuItem disabled={l.read_only} onSelect={() => { close(); p.onListMenu(l, 'rename') }}>{tr('common.rename')}</MenuItem>
                      {/* ACT list.set_default @web.tasks */}
                      <MenuItem checked={p.defaultList === l.id} onSelect={() => { close(); p.onListMenu(l, 'default') }}>{tr('tasks.sidebar.defaultList')}</MenuItem>
                      <MenuSep />
                      <div className="menu-header">{tr('common.colour')}</div>
                      {/* ACT list.color @web.tasks */}
                      <ColorRow current={l.color} onPick={(c) => { close(); p.onListMenu(l, 'color', c) }} />
                      <MenuSep />
                      {/* ACT list.delete @web.tasks */}
                      <MenuItem danger disabled={l.read_only} onSelect={() => { close(); p.onListMenu(l, 'delete') }}>{tr('tasks.sidebar.deleteList')}</MenuItem>
                    </>
                  )}
                </ItemMenu>
              }
            />
          )
        })}
        {/* ACT list.create @web.tasks */}
        <button type="button" className="tk-side-new" onClick={p.onNewList}><Plus size={15} aria-hidden />{' '}{tr('common.newList')}</button>
      </div>

      {p.tags.length > 0 && (
        <>
          <div className="tk-side-head">
            <button type="button" className="tk-side-toggle" aria-expanded={showTags} onClick={() => setShowTags((x) => !x)}>{tr('common.tags')}</button>
          </div>
          {showTags && (
            <div className="tk-side-group" role="group" aria-label={tr('common.tags')}>
              {p.tags.map((t) => (
                <Entry
                  key={t.name}
                  to={tasksPath({ kind: 'tag', id: t.name })}
                  icon={<Tag style={{ color: p.tagColor(t.name) ?? undefined }} />}
                  label={t.name}
                  count={p.counts?.tags[t.name] ?? t.count ?? null}
                  active={isOn({ kind: 'tag', id: t.name }) || (cur?.kind === 'tag' && cur.id.toLowerCase() === t.name.toLowerCase())}
                  accept
                  onDrop={(uids) => p.onDropTag(uids, t.name)}
                  testid={`tasks-nav-tag-${t.name}`}
                  onNavigate={p.onNavigate}
                  menu={
                    <ItemMenu label={tr('tasks.sidebar.actionsForTag', { itemName: t.name })}>
                      {(close) => (
                        <>
                          {/* ACT tag.rename @web.tasks */}
                          <MenuItem onSelect={() => { close(); p.onTagMenu(t, 'rename') }}>{tr('common.rename')}</MenuItem>
                          <div className="menu-header">{tr('common.colour')}</div>
                          {/* ACT tag.color @web.tasks */}
                          <ColorRow current={p.tagColor(t.name)} noColor onPick={(c) => { close(); p.onTagMenu(t, 'color', c) }} />
                          <MenuSep />
                          {/* ACT tag.delete @web.tasks */}
                          <MenuItem danger onSelect={() => { close(); p.onTagMenu(t, 'delete') }}>{tr('tasks.sidebar.deleteTag')}</MenuItem>
                        </>
                      )}
                    </ItemMenu>
                  }
                />
              ))}
            </div>
          )}
        </>
      )}
    </nav>
  )
}

// The list header: the list name (renamed inline for user lists), the
// count, Sort, Group, Show completed, Show deferred (Today and All), and ⋮ with Select and Delete
// completed. Search is the top bar's box on the Tasks scope; "/" focuses it.

import { ArrowUpDown, Eye, Layers, ListChecks, Lock, Menu, MoreVertical, Trash2 } from 'lucide-react'
import { forwardRef, useImperativeHandle, useState, type ReactNode } from 'react'
import { MenuHeader, MenuItem, MenuSep, Popover, useAnchor } from '../../components/Popover'
import { focusSearch } from '../search/SearchBar'
import type { GroupKey, SortKey } from './model'
import type { ViewPrefs } from './store'
import { t } from '../../i18n'

export interface HeaderHandle {
  focusSearch: () => void
}

interface Props {
  title: string
  icon?: ReactNode
  color?: string | null
  count: number | null
  overdue?: number
  renamable: boolean
  readOnly: boolean
  prefs: ViewPrefs
  defaultSortLabel: string
  canDeferred: boolean
  canCompleted: boolean
  canGroup: boolean
  narrow: boolean
  onMenu?: () => void
  onRename: (name: string) => void
  onPrefs: (p: Partial<ViewPrefs>) => void
  onSelectMode: () => void
  onClearCompleted?: () => void
}

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'manual', label: t('common.manual') }, { id: 'due', label: t('tasks.header.dueDate') }, { id: 'priority', label: t('common.priority') }, { id: 'title', label: t('common.title') }, { id: 'created', label: t('common.created') },
]
const GROUPS: { id: GroupKey; label: string }[] = [
  { id: 'none', label: t('common.none') }, { id: 'due', label: t('tasks.header.dueDate') }, { id: 'priority', label: t('common.priority') }, { id: 'list', label: t('common.list') }, { id: 'tag', label: t('common.tag') },
]

export const Header = forwardRef<HeaderHandle, Props>(function Header(p, ref) {
  const sortA = useAnchor<HTMLButtonElement>()
  const groupA = useAnchor<HTMLButtonElement>()
  const moreA = useAnchor<HTMLButtonElement>()
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(p.title)

  useImperativeHandle(ref, () => ({
    focusSearch: () => focusSearch('tasks'),
  }))

  const sortLabel = p.prefs.sort === 'auto' ? p.defaultSortLabel : SORTS.find((s) => s.id === p.prefs.sort)?.label
  return (
    <header className="tk-head" data-testid="tasks-header">
      {p.narrow && (
        <button type="button" className="icon-btn" aria-label={t('tasks.header.lists')} onClick={p.onMenu}><Menu size={20} /></button>
      )}
      <div className="tk-head-title">
        {p.icon && <span className="tk-head-icon" aria-hidden style={p.color ? { color: p.color } : undefined}>{p.icon}</span>}
        {renaming ? (
          // ACT list.rename @web.tasks
          <input className="tk-rename" value={name} autoFocus aria-label={t('tasks.header.listName')} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') {
                setRenaming(false)
                if (name.trim() && name.trim() !== p.title) p.onRename(name.trim())
              } else if (e.key === 'Escape') {
                setRenaming(false)
                setName(p.title)
              }
            }}
            onBlur={() => {
              setRenaming(false)
              if (name.trim() && name.trim() !== p.title) p.onRename(name.trim())
            }} />
        ) : (
          <h1 className={p.renamable ? 'renamable' : ''} title={p.renamable ? t('tasks.header.clickToRename') : undefined} onClick={() => {
            if (!p.renamable) return
            setName(p.title)
            setRenaming(true)
          }}>{p.title}</h1>
        )}
        {p.readOnly && <Lock size={15} className="muted" aria-label={t('tasks.header.readOnlyList')} />}
        {p.count != null && <span className="tk-head-count" data-testid="tasks-count">{p.count}</span>}
        {!!p.overdue && <span className="tk-head-overdue">{t('tasks.header.overdue', { overdue: p.overdue })}</span>}
      </div>
      <div className="tk-head-tools">
        <button type="button" className="tk-tool" onClick={(e) => sortA.toggle(e.currentTarget)} aria-label={t('tasks.header.sort', { sortLabel })}>
          <ArrowUpDown size={15} aria-hidden /><span className="tk-tool-text">{sortLabel}</span>
        </button>
        {p.canGroup && (
          <button type="button" className="tk-tool" onClick={(e) => groupA.toggle(e.currentTarget)} aria-label={t('common.group')}>
            <Layers size={15} aria-hidden /><span className="tk-tool-text">{p.prefs.group === 'auto' ? t('common.group') : GROUPS.find((g) => g.id === p.prefs.group)?.label}</span>
          </button>
        )}
        <button type="button" className="icon-btn" aria-label={t('tasks.header.moreListActions')} onClick={(e) => moreA.toggle(e.currentTarget)}><MoreVertical size={18} /></button>
      </div>

      <Popover anchor={sortA.anchor} open={sortA.open} onClose={sortA.close} label={t('common.sort')}>
        <MenuHeader>{t('tasks.header.sortBy')}</MenuHeader>
        <MenuItem checked={p.prefs.sort === 'auto'} onSelect={() => { sortA.close(); p.onPrefs({ sort: 'auto' }) }}>{t('tasks.header.default', { defaultSortLabel: p.defaultSortLabel })}</MenuItem>
        {SORTS.map((s) => (
          <MenuItem key={s.id} checked={p.prefs.sort === s.id} onSelect={() => { sortA.close(); p.onPrefs({ sort: s.id }) }}>{s.label}</MenuItem>
        ))}
      </Popover>
      <Popover anchor={groupA.anchor} open={groupA.open} onClose={groupA.close} label={t('common.group')}>
        <MenuHeader>{t('common.groupBy')}</MenuHeader>
        <MenuItem checked={p.prefs.group === 'auto'} onSelect={() => { groupA.close(); p.onPrefs({ group: 'auto' }) }}>{t('common.default')}</MenuItem>
        {GROUPS.map((g) => (
          <MenuItem key={g.id} checked={p.prefs.group === g.id} onSelect={() => { groupA.close(); p.onPrefs({ group: g.id }) }}>{g.label}</MenuItem>
        ))}
      </Popover>
      <Popover anchor={moreA.anchor} open={moreA.open} onClose={moreA.close} label={t('tasks.header.moreListActions')}>
        {p.canCompleted && <MenuItem icon={<Eye size={16} />} checked={p.prefs.showCompleted} onSelect={() => { moreA.close(); p.onPrefs({ showCompleted: !p.prefs.showCompleted }) }}>{t('tasks.header.showCompleted')}</MenuItem>}
        {p.canDeferred && <MenuItem icon={<Eye size={16} />} checked={p.prefs.showDeferred} onSelect={() => { moreA.close(); p.onPrefs({ showDeferred: !p.prefs.showDeferred }) }}>{t('tasks.header.showDeferred')}</MenuItem>}
        <MenuSep />
        <MenuItem icon={<ListChecks size={16} />} shortcut="x" onSelect={() => { moreA.close(); p.onSelectMode() }}>{t('common.select')}</MenuItem>
        {/* ACT task.clear_completed @web.tasks */}
        {p.onClearCompleted && <MenuItem icon={<Trash2 size={16} />} danger onSelect={() => { moreA.close(); p.onClearCompleted?.() }}>{t('tasks.header.deleteCompleted')}</MenuItem>}
      </Popover>
    </header>
  )
})

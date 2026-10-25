// The summary chip of a contact group in a recipient field (group.expand): the
// group's name and how many of its members are still there; it opens to the members, each with
// its own remove button, and its × removes the members the group added.

import { ChevronDown, ChevronUp, Users, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { groupLabel, type Shown } from './groupChips'
import './people.css'
import { t } from '../../i18n'

export function GroupChip<T extends { email: string; name?: string }>({ shown, open, onToggle, onRemove, renderMember, className = 'recip-chip' }: {
  shown: Shown<T>
  open: boolean
  onToggle: () => void
  onRemove: () => void
  renderMember: (m: T) => ReactNode
  className?: string
}) {
  const { group, members } = shown
  return (
    <span className="group-chip-wrap" data-testid="group-chip">
      <span className={`${className} group-chip`} title={members.map((m) => m.email).join(', ')}>
        <Users size={14} aria-hidden className="group-chip-icon" />
        <button type="button" className="group-chip-name" aria-expanded={open} aria-label={t('people.groupChip.showTheMembersOf', { groupName: group.name })}
          onClick={(e) => { e.stopPropagation(); onToggle() }}>
          <span className="nm">{groupLabel(group, members.length)}</span>
          {open ? <ChevronUp size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
        </button>
        <button type="button" aria-label={t('people.groupChip.removeTheGroup', { groupName: group.name })} onClick={(e) => { e.stopPropagation(); onRemove() }}>
          <X />
        </button>
      </span>
      {open && <span className="group-chip-members" role="group" aria-label={t('people.groupChip.membersOf', { groupName: group.name })}>{members.map(renderMember)}</span>}
    </span>
  )
}

/** One option row for a group in an autocomplete list. */
export function GroupOption({ name, count, active, onPick, className = 'suggest-item' }: { name: string; count: number; active: boolean; onPick: () => void; className?: string }) {
  return (
    <button type="button" role="option" aria-selected={active} data-active={active} className={`${className} group-option${active ? ' active' : ''}`}
      onMouseDown={(e) => e.preventDefault()} onClick={onPick}>
      <span className="group-option-icon" aria-hidden><Users size={16} /></span>
      <span className="who">
        <span>{name}</span>
        <span>{t('people.groupChip.group', { value: t('people.groupChip.people', { count }) })}</span>
      </span>
    </button>
  )
}

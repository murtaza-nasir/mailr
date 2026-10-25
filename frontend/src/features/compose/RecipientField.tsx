import { X } from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { usePeople } from '../../api/hooks'
import type { Address } from '../../api/types'
import { Avatar } from '../../components/bits'
import { displayName, isValidEmail, parseAddressList } from '../../lib/format'
import { useGroups } from '../people/api'
import { useSearchAnswer } from '../people/searchAnswer'
import { GroupChip, GroupOption } from '../people/GroupChip'
import { rankPeople } from './recipientRank'
import { addGroup, groupOptions, partition, prune, removeGroup, type Expanded } from '../people/groupChips'
import { t as tr } from '../../i18n'

interface Props {
  label: string
  value: Address[]
  onChange: (v: Address[]) => void
  autoFocus?: boolean
  trailing?: ReactNode
  onSubmit?: () => void
}

// People come ranked by strength (ACT person.find @web.editor): recent two-way correspondents
// first. Groups whose name matches are offered above them; choosing one adds its members
// (ACT group.expand @web.editor).
export function RecipientField({ label, value, onChange, autoFocus, trailing, onSubmit }: Props) {
  const [text, setText] = useState('')
  const [active, setActive] = useState(0)
  const [focused, setFocused] = useState(false)
  const [expanded, setExpanded] = useState<Expanded[]>([])
  const [openGroup, setOpenGroup] = useState<number | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const q = focused ? text.trim() : ''
  const people = usePeople(q)
  const groupsQ = useGroups(q, 'active', q.length > 0)
  // Only suggestions for what is typed now (never the last search's); Enter before they arrive acts when they do.
  const search = useSearchAnswer(q, [people, groupsQ], () => {
    if (total > 0) pick(Math.min(active, total - 1))
    else commitText()
  })
  const answered = search.answered
  const groups = answered ? groupsQ.data ?? [] : []
  const existing = new Set(value.map((a) => a.email.toLowerCase()))
  const groupOpts = groupOptions(groups, q, expanded)
  const suggestions = answered ? rankPeople((people.data ?? []).filter((p) => !existing.has(p.email.toLowerCase())), q).slice(0, 6) : []
  const total = groupOpts.length + suggestions.length
  const showSuggest = focused && text.trim().length > 0 && total > 0
  const chips = partition(value, prune(value, expanded))

  const change = (v: Address[]) => {
    setExpanded((x) => prune(v, x))
    onChange(v)
  }

  const add = (list: Address[]) => {
    const merged = [...value]
    for (const a of list) if (!merged.some((m) => m.email.toLowerCase() === a.email.toLowerCase())) merged.push(a)
    onChange(merged)
    setText('')
    setActive(0)
  }

  const pickGroup = (i: number) => {
    const g = groupOpts[i]
    const res = addGroup(value, g, g.members.map((m) => ({ email: m.email, name: m.name })))
    setExpanded((x) => [...prune(value, x), res.expanded])
    onChange(res.value)
    setText('')
    setActive(0)
  }

  const pick = (i: number) => {
    if (i < groupOpts.length) pickGroup(i)
    else {
      const p = suggestions[i - groupOpts.length]
      add([{ email: p.email, name: p.name }])
    }
  }

  const commitText = () => {
    const t = text.trim().replace(/[,;]$/, '')
    if (!t) return false
    const parsed = parseAddressList(t)
    if (parsed.length) add(parsed)
    else add([{ email: t }]) // kept as an invalid chip so the user sees it
    return true
  }


  // Ctrl+Enter here sends with what is typed: the text becomes a chip first, and Send runs on the
  // next render, when the message holds it (an invalid chip then stops Send, as it does on click).
  const [submitAfter, setSubmitAfter] = useState(false)
  useEffect(() => {
    if (!submitAfter) return
    setSubmitAfter(false)
    onSubmit?.()
  }, [submitAfter]) // eslint-disable-line react-hooks/exhaustive-deps -- once, with the new recipients

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      if (commitText()) setSubmitAfter(true)
      else onSubmit?.()
      return
    }
    if (showSuggest && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault()
      setActive((a) => (e.key === 'ArrowDown' ? Math.min(a + 1, total - 1) : Math.max(a - 1, 0)))
      return
    }
    if (e.key === 'Enter' && q && !search.ready) {
      e.preventDefault()
      search.deferEnter()
      return
    }
    if (e.key === 'Enter' || e.key === 'Tab' || e.key === ',' || e.key === ';') {
      if (showSuggest && (e.key === 'Enter' || e.key === 'Tab')) {
        e.preventDefault()
        pick(Math.min(active, total - 1))
        return
      }
      if (text.trim()) {
        if (e.key !== 'Tab') e.preventDefault()
        commitText()
      }
      return
    }
    if (e.key === 'Backspace' && !text && value.length) {
      change(value.slice(0, -1))
    }
    if (e.key === 'Escape' && showSuggest) {
      e.stopPropagation()
      setText('')
    }
  }

  const chip = (a: Address) => {
    const valid = isValidEmail(a.email)
    return (
      // The address shows on hover and on keyboard focus of the chip's button: a link can give
      // any address a familiar name, and the chip itself shows the name only.
      <span key={a.email} className={`recip-chip${valid ? '' : ' invalid'}`} data-testid="recip-chip" data-email={a.email}
        data-tip={valid ? (a.name ? `${displayName(a)} <${a.email}>` : a.email) : tr('compose.recipientField.isNotAValidAddress', { email: a.email })}>
        <Avatar who={a} size="xs" />
        <span className="nm" dir="auto">{a.name ? displayName(a) : a.email}</span>
        <button type="button" aria-label={tr('compose.recipientField.remove', { email: a.email })} onClick={(e) => { e.stopPropagation(); change(value.filter((x) => x.email !== a.email)) }}>
          <X />
        </button>
      </span>
    )
  }

  return (
    <div className="compose-field" onClick={() => inputRef.current?.focus()}>
      <span className="lbl">{label}</span>
      <div className="recips">
        {chips.groups.map((s) => (
          <GroupChip key={`g${s.group.id}`} shown={s} open={openGroup === s.group.id}
            onToggle={() => setOpenGroup((o) => (o === s.group.id ? null : s.group.id))}
            onRemove={() => { setExpanded((x) => x.filter((g) => g !== s.group)); onChange(removeGroup(value, s.group)) }}
            renderMember={chip} />
        ))}
        {chips.loose.map(chip)}
        <input
          ref={inputRef}
          value={text}
          autoFocus={autoFocus}
          aria-label={tr('compose.recipientField.recipients', { label })}
          autoComplete="off"
          onChange={(e) => {
            setText(e.target.value)
            setActive(0)
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => {
            setFocused(false)
            commitText()
          }}
          onPaste={(e) => {
            const t = e.clipboardData.getData('text')
            const parsed = parseAddressList(t)
            if (parsed.length > 1 || (parsed.length === 1 && t.includes('<'))) {
              e.preventDefault()
              add(parsed)
            }
          }}
        />
        {showSuggest && (
          <div className="suggest" role="listbox">
            {groupOpts.map((g, i) => <GroupOption key={`g${g.id}`} name={g.name} count={g.count} active={i === active} onPick={() => pickGroup(i)} />)}
            {suggestions.map((p, j) => {
              const i = j + groupOpts.length
              return (
                <button
                  type="button"
                  key={p.email}
                  className="suggest-item"
                  role="option"
                  aria-selected={i === active}
                  data-active={i === active}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => add([{ email: p.email, name: p.name }])}
                >
                  <Avatar who={p} size="sm" />
                  <span className="who">
                    <span dir="auto">{displayName(p)}</span>
                    <span>{p.email}</span>
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>
      {trailing}
    </div>
  )
}

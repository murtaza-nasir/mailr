// `/name` autocomplete for skills, shared by the assistant input and Help me write.
//
// Typing `/` (at the start or after a space) opens a list of the user's enabled skills with their
// descriptions; ↑/↓ move, Enter or Tab inserts `/name `, Escape closes. The server resolves the
// tokens. In the assistant's message box (`withCommands`), a `/` that opens the message also lists
// the chat's commands (commands.ts), first.

import { useQuery } from '@tanstack/react-query'
import { Sparkles, SquareSlash } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from 'react'
import { api } from '../../../api/client'
import { t } from '../../../i18n'
import { matchCommands, useCommands } from './commands'

interface SkillLite {
  name: string
  title: string
  description: string
  enabled: boolean
  auto: boolean
}

export function useSkills() {
  return useQuery({
    queryKey: ['assistant', 'skills'],
    queryFn: () => api.get<{ items: SkillLite[] }>('/assistant/skills'),
    staleTime: 60_000,
  })
}

const TOKEN = /(^|[\s(])(\/\/?)([a-z0-9_-]*)$/i

/** The `/prefix` being typed at the caret, if any. */
export function tokenAt(text: string, caret: number): { start: number; prefix: string } | null {
  const m = TOKEN.exec(text.slice(0, caret))
  if (!m) return null
  return { start: caret - m[3].length - m[2].length, prefix: m[3].toLowerCase() }
}

interface PickItem {
  kind: 'command' | 'skill'
  name: string
  desc: string
  /** A command that takes no text: Enter on its whole name sends the message instead of inserting it. */
  complete?: boolean
}

export function useSkillPicker(value: string, setValue: (v: string) => void, ref: RefObject<HTMLTextAreaElement | null>,
  withCommands = false) {
  const skills = useSkills().data?.items
  const commands = useCommands().data?.items
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(0)
  const [dismissedAt, setDismissedAt] = useState<number | null>(null)
  const tok = tokenAt(value, caret)
  const leading = tok !== null && withCommands && value.slice(0, tok.start).trim() === '' && !value.startsWith('//', tok.start)
  const matches = useMemo((): PickItem[] => {
    if (!tok) return []
    const cmds: PickItem[] = leading
      ? matchCommands(commands, tok.prefix).map((c) => ({ kind: 'command', name: c.name, desc: c.summary,
        complete: c.arg === 'none' && (c.name === tok.prefix || c.aliases.includes(tok.prefix)) }))
      : []
    const sk: PickItem[] = (skills ?? []).filter((s) => s.enabled && !s.auto && s.name.startsWith(tok.prefix))
      .map((s) => ({ kind: 'skill', name: s.name, desc: s.description || s.title }))
    return [...cmds, ...sk].slice(0, leading ? 12 : 8)
  }, [tok, skills, commands, leading])
  const open = matches.length > 0 && tok !== null && dismissedAt !== tok.start
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => setActive(0), [tok?.prefix])

  const sync = useCallback(() => {
    const el = ref.current
    if (el) setCaret(el.selectionStart ?? el.value.length)
  }, [ref])

  const pick = useCallback(
    (name: string) => {
      if (!tok) return
      const before = value.slice(0, tok.start)
      const after = value.slice(caret).replace(/^[a-z0-9_-]*/i, '')
      const insert = `/${name} `
      const next = before + insert + after.replace(/^\s/, '')
      setValue(next)
      const pos = before.length + insert.length
      requestAnimationFrame(() => {
        const el = ref.current
        if (el) {
          el.focus()
          el.setSelectionRange(pos, pos)
          setCaret(pos)
        }
      })
    },
    [tok, value, caret, setValue, ref],
  )

  /** Call first in the textarea's onKeyDown; true when the picker used the key. */
  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!open) return false
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setActive((i) => (i + (e.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length)
        return true
      }
      if (e.key === 'Enter' && !e.shiftKey && (matches[active] ?? matches[0]).complete) {
        setDismissedAt(tok?.start ?? null)
        return false // the message box sends `/new`, `/help`…
      }
      if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
        e.preventDefault()
        e.stopPropagation()
        pick(matches[active]?.name ?? matches[0].name)
        return true
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setDismissedAt(tok?.start ?? null)
        return true
      }
      return false
    },
    [open, matches, active, pick, tok],
  )

  const popover = open ? (
    <ul className="skill-pop" role="listbox" aria-label={leading ? t('rail.assistant.commands.label') : t('common.skills')} ref={listRef}>
      {matches.map((s, i) => (
        <li
          key={`${s.kind}:${s.name}`}
          role="option"
          aria-selected={i === active}
          className={i === active ? 'active' : ''}
          onMouseDown={(e) => {
            e.preventDefault()
            pick(s.name)
          }}
          onMouseEnter={() => setActive(i)}
        >
          {s.kind === 'command' ? <SquareSlash size={14} aria-hidden /> : <Sparkles size={14} aria-hidden />}
          <span className="skill-pop-name">/{s.name}</span>
          <span className="skill-pop-desc">{s.desc}</span>
        </li>
      ))}
      <li className="skill-pop-hint" aria-hidden>
        {t('rail.assistant.skillPicker.toChooseEnterToInsert')}
      </li>
    </ul>
  ) : null

  return { onKeyDown, sync, popover, open }
}

// The one search box: "Search" with a scope chip, All · Mail · Calendar · Tasks ·
// Notes. It starts on the app you are in and Esc returns it to All. Mail and All keep the mail
// grammar (from:, to:me, has:attachment…) and go to the mail results; All adds the Events, Tasks and
// Notes groups in the order of the app you searched from. Calendar, Tasks and Notes search in their
// own app as you type, as their own boxes did; their "/" key and toolbar Search focus this box.
// ACT event.search @web.calendar, ACT task.search @web.tasks, ACT note.search @web.notes (the box pre-scoped to the app)

import { ArrowLeft, ChevronDown, Clock, Folder, Search, SlidersHorizontal, Tag, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import { useLabels, usePeople } from '../../api/hooks'
import { Avatar } from '../../components/bits'
import { MenuItem, Popover, useAnchor } from '../../components/Popover'
import { displayName } from '../../lib/format'
import { calendarSearchPath, notesPath } from '../../lib/routes'
import { mailPath, viewAccount } from '../../lib/views'
import { scopeToAccount } from '../../lib/search'
import { useMailAccounts } from '../layout/accounts'
import { useTasksUI } from '../tasks/store'
import { AdvancedSearchForm } from './AdvancedSearchForm'
import { IN_FOLDERS, lastToken, matchNames, operatorValue, operatorWord } from './operatorSuggest'
import { appScopeOf, SCOPES, scopeLabel, useSearchResults, type SearchScope } from './scope'
import { t as tr } from '../../i18n'

const RECENT_KEY = 'mailr.recentSearches'

function loadRecent(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(v) ? v.slice(0, 6) : []
  } catch {
    return []
  }
}

function saveRecent(q: string) {
  try {
    const list = [q, ...loadRecent().filter((x) => x !== q)].slice(0, 6)
    localStorage.setItem(RECENT_KEY, JSON.stringify(list))
  } catch {
    /* ignore */
  }
}

/** Ask the box for the focus, on a scope ("/" in Calendar, Tasks and Notes). */
export function focusSearch(scope?: SearchScope) {
  window.dispatchEvent(new CustomEvent('mailr:focus-search', { detail: scope }))
}

// 'complete': a value after an operator (from:, label:, in:); picking it completes the word in the box.
type Suggestion = { kind: 'person' | 'recent' | 'search' | 'complete'; text: string; q: string; email?: string; name?: string; icon?: 'person' | 'label' | 'folder' }
type AppScope = 'calendar' | 'tasks' | 'notes'
const isAppScope = (s: SearchScope): s is AppScope => s === 'calendar' || s === 'tasks' || s === 'notes'

export function SearchBar() {
  const [params] = useSearchParams()
  const location = useLocation()
  const nav = useNavigate()
  const path = location.pathname
  const app = appScopeOf(path)
  const results = useSearchResults()
  // Inside an account's own folder a mail search stays in that account (account:<address>; the
  // results show it as a chip that takes it out). ACT mail.search_account @web.list
  const { byId } = useMailAccounts()
  const inAccount = byId.get(viewAccount(decodeURIComponent(/^\/mail\/([^/]+)/.exec(location.pathname)?.[1] ?? '')) ?? -1)?.address
  const tasksSearch = useTasksUI((s) => s.search)
  const onMailSearch = path.startsWith('/mail/search')
  const appSearchPage = path === '/calendar/search' || path === '/notes/search'
  // The search the page shows, whatever box started it.
  const urlQ = app === 'mail' || appSearchPage ? (params.get('q') ?? '') : app === 'tasks' ? tasksSearch.trim() : ''
  // The scope the page is in: the mail results say All or Mail, every other page its app.
  const pageScope: SearchScope = onMailSearch ? results.scope : app
  const [scope, setScope] = useState<SearchScope>(pageScope)
  const [value, setValue] = useState(urlQ)
  const [focused, setFocused] = useState(false)
  const [adv, setAdv] = useState(false)
  const [active, setActive] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const typed = useRef(false)
  const chip = useAnchor()

  // Follow the page: another app or another search resets the chip, and the text unless you are
  // typing in the box.
  const page = `${pageScope}|${urlQ}`
  const [lastPage, setLastPage] = useState(page)
  if (page !== lastPage) {
    const [prevScope] = lastPage.split('|')
    setLastPage(page)
    if (prevScope !== pageScope) setScope(pageScope)
    if (!focused || prevScope !== pageScope) setValue(urlQ)
  }

  const mailLike = scope === 'all' || scope === 'mail'
  const trimmed = value.trim()
  const lastWord = trimmed.split(/\s+/).pop() ?? ''
  // The last word is an operator being typed (from:mei, label:Blue, in:sp): its values come first.
  const opWord = useMemo(() => (mailLike ? operatorWord(lastToken(value)) : null), [mailLike, value])
  const peopleOp = opWord && opWord.op !== 'label' && opWord.op !== 'in' ? opWord : null
  const people = usePeople(focused && mailLike ? (peopleOp ? (peopleOp.partial.length >= 1 ? peopleOp.partial : '') : lastWord.length >= 2 && !lastWord.includes(':') ? lastWord : '') : '')
  const labels = useLabels().data
  const suggestions = useMemo(() => {
    if (!focused || adv) return [] as Suggestion[]
    if (!trimmed) return loadRecent().map((r): Suggestion => ({ kind: 'recent', text: r, q: r }))
    const out: Suggestion[] = []
    if (opWord) {
      const head = value.slice(0, value.length - lastToken(value).length)
      const done = (word: string) => `${head}${word} `
      if (opWord.op === 'label') {
        const names = (labels ?? []).filter((l) => !l.role).map((l) => l.name)
        for (const n of matchNames(names, opWord.partial)) out.push({ kind: 'complete', icon: 'label', text: operatorValue('label', n), q: done(operatorValue('label', n)) })
      } else if (opWord.op === 'in') {
        for (const f of matchNames(IN_FOLDERS, opWord.partial)) out.push({ kind: 'complete', icon: 'folder', text: `in:${f}`, q: done(`in:${f}`) })
      } else {
        for (const p of (opWord.partial ? people.data ?? [] : []).slice(0, 6)) {
          out.push({ kind: 'complete', icon: 'person', text: displayName(p), email: p.email, name: p.name, q: done(`${opWord.op}:${p.email}`) })
        }
      }
      if (out.length) return out
    }
    if (mailLike && !opWord) {
      for (const p of (people.data ?? []).slice(0, 5)) {
        const head = trimmed.slice(0, trimmed.length - lastWord.length)
        out.push({ kind: 'person', text: displayName(p), email: p.email, name: p.name, q: `${head}from:${p.email}`.trim() })
      }
    }
    out.push({ kind: 'search', text: scope === 'all' ? tr('search.searchBar.searchEverythingFor', { trimmed }) : tr('search.searchBar.searchFor', { scopeLabel: scopeLabel(scope).toLowerCase(), trimmed }), q: trimmed })
    return out
  }, [focused, adv, trimmed, lastWord, people.data, mailLike, scope, opWord, labels, value])

  useEffect(() => {
    const focus = (e: Event) => {
      const want = (e as CustomEvent<SearchScope | undefined>).detail
      if (want) setScope(want)
      inputRef.current?.focus()
    }
    window.addEventListener('mailr:focus-search', focus)
    return () => window.removeEventListener('mailr:focus-search', focus)
  }, [])

  useEffect(() => {
    if (!focused && !adv) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node) && !(e.target as HTMLElement).closest('.popover')) {
        setFocused(false)
        setAdv(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [focused, adv])

  /** Search in Calendar, Tasks or Notes: their own results, as their boxes did. */
  const runApp = (s: AppScope, q: string, replace: boolean) => {
    if (s === 'calendar') {
      if (q) nav(calendarSearchPath(q), { replace: replace && path === '/calendar/search' })
      else if (path === '/calendar/search') nav('/calendar')
    } else if (s === 'notes') {
      if (q) nav(notesPath('search', q), { replace: replace && path === '/notes/search' })
      else if (path === '/notes/search') nav(notesPath('all'))
    } else {
      useTasksUI.getState().setSearch(q)
      if (app !== 'tasks' && q) nav('/tasks/all')
    }
  }

  // Calendar, Tasks and Notes search as you type, after a pause, in the app you are in.
  const live = scope === app && isAppScope(scope)
  useEffect(() => {
    if (!live || !typed.current) return
    const t = setTimeout(() => {
      typed.current = false
      runApp(scope as AppScope, value.trim(), true)
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, live])

  const submit = (q: string, s: SearchScope = scope) => {
    const t = q.trim()
    typed.current = false
    setFocused(false)
    setAdv(false)
    setActive(-1)
    inputRef.current?.blur()
    if (isAppScope(s)) {
      if (t) saveRecent(t)
      runApp(s, t, false)
      return
    }
    if (!t) {
      nav(mailPath('inbox'))
      return
    }
    saveRecent(t)
    results.set(s === 'all' ? { scope: 'all', from: app } : { scope: 'mail', from: 'mail' })
    nav(mailPath('search', null, scopeToAccount(t, inAccount)))
  }

  /** An operator's value picked: the word is complete in the box; Enter searches. */
  const complete = (q: string) => {
    typed.current = true
    setValue(q)
    setActive(-1)
    inputRef.current?.focus()
  }

  const pickScope = (s: SearchScope) => {
    chip.close()
    setScope(s)
    // A search on the page moves with the chip; otherwise the box waits for words.
    if (urlQ && trimmed) submit(trimmed, s)
    else inputRef.current?.focus()
  }

  const clear = () => {
    setValue('')
    if (app === 'mail') submit('', 'mail')
    else if (isAppScope(app)) runApp(app, '', false)
  }

  const open = (focused && suggestions.length > 0) || adv
  const label = scope === 'all' ? tr('common.search') : tr('search.searchBar.search', { scopeLabel: scopeLabel(scope).toLowerCase() })

  return (
    <div className="search" ref={wrapRef} role="search">
      <div className={`search-box${focused ? ' focused' : ''}${open ? ' open' : ''}`}>
        {urlQ ? (
          <button type="button" className="icon-btn" aria-label={tr('common.back')} data-tip={app === 'mail' ? tr('search.searchBar.backToInbox') : tr('search.searchBar.clearTheSearch')} onClick={clear}>
            <ArrowLeft />
          </button>
        ) : (
          <button type="button" className="icon-btn" aria-label={tr('common.search')} data-tip={tr('common.search')} onClick={() => submit(value)}>
            <Search />
          </button>
        )}
        <input
          ref={inputRef}
          value={value}
          placeholder={tr('common.search')}
          aria-label={label}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          data-testid="search-input"
          onChange={(e) => {
            typed.current = true
            setValue(e.target.value)
            setActive(-1)
            setAdv(false)
          }}
          onFocus={() => setFocused(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              const s = active >= 0 ? suggestions[active] : undefined
              if (s?.kind === 'complete') complete(s.q)
              else submit(s ? s.q : value)
            } else if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActive((a) => Math.min(a + 1, suggestions.length - 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((a) => Math.max(a - 1, -1))
            } else if (e.key === 'Escape') {
              e.stopPropagation()
              // Esc: first back to All, then out of the box.
              if (scope !== 'all') {
                setScope('all')
                return
              }
              setFocused(false)
              setAdv(false)
              inputRef.current?.blur()
            }
          }}
        />
        {value && (
          <button type="button" className="icon-btn" aria-label={tr('search.searchBar.clearSearch')} data-tip={tr('search.searchBar.clearSearch')} onClick={() => {
            setValue('')
            // A search the app shows clears there too; otherwise the box only empties.
            if (urlQ && isAppScope(app)) runApp(app, '', true)
            inputRef.current?.focus()
          }}>
            <X />
          </button>
        )}
        <button type="button" className="search-scope" aria-label={tr('search.searchBar.searchIn', { scopeLabel: scopeLabel(scope) })} aria-haspopup="menu" aria-expanded={chip.open}
          data-tip={tr('search.searchBar.whereToSearchEscAll')} data-testid="search-scope" onMouseDown={(e) => e.preventDefault()} onClick={(e) => chip.toggle(e.currentTarget)}>
          <span>{scopeLabel(scope)}</span>
          <ChevronDown size={14} aria-hidden />
        </button>
        <Popover anchor={chip.anchor} open={chip.open} onClose={chip.close} placement="bottom-end" label={tr('search.searchBar.searchIn2')} minWidth={160}>
          {SCOPES.map((s) => (
            <MenuItem key={s.id} checked={s.id === scope} onSelect={() => pickScope(s.id)}>{s.label}</MenuItem>
          ))}
        </Popover>
        {mailLike && (
          <button type="button" className={`icon-btn search-adv${adv ? ' active' : ''}`} aria-label={tr('search.searchBar.showSearchOptions')} data-tip={tr('search.searchBar.showSearchOptions')} aria-expanded={adv} onClick={() => { setAdv(!adv); setFocused(false) }}>
            <SlidersHorizontal />
          </button>
        )}
      </div>
      {open && (
        <div className="search-panel" role="listbox">
          {adv ? (
            <AdvancedSearchForm
              initialQuery={value}
              onSearch={(q) => submit(q, scope === 'all' ? 'all' : 'mail')}
              onCreateFilter={(q) => {
                setAdv(false)
                nav(`/settings/mail/filters?new=${encodeURIComponent(q)}`)
              }}
            />
          ) : (
            suggestions.map((s, i) => (
              <button
                type="button"
                key={`${s.kind}-${s.q}`}
                className="menu-item"
                role="option"
                aria-selected={i === active}
                data-active={i === active}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => (s.kind === 'complete' ? complete(s.q) : submit(s.q))}
              >
                {s.kind === 'person' || s.icon === 'person' ? <Avatar who={{ email: s.email!, name: s.name }} size="xs" />
                  : s.icon === 'label' ? <Tag /> : s.icon === 'folder' ? <Folder /> : s.kind === 'recent' ? <Clock /> : <Search />}
                <span className="ellipsis">
                  <bdi>{s.text}</bdi>
                  {(s.kind === 'person' || s.icon === 'person') && <span className="muted"> &nbsp;{s.email}</span>}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}

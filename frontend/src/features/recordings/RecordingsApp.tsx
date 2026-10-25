// The Recordings app (web.recordings): every
// Speakr recording in mailr's mirror, in one place. The left column has All, Recent (seven days),
// Linked to events, Not linked, Speakr's folders and tags with counts, and a participants filter.
// Participants are people: each string Speakr kept resolves to a person on the server
// (participants.py), so "Alex" and "Alex Rivera" are one row, You pinned first. A row's menu
// merges a person into another (Same person as…) or separates a merged name; suggested merges of
// a first name into a full name show above the list with Merge and Dismiss.
// The list is grouped by day; a row shows the title, time, length, participants (with People's
// avatars where mailr knows the address), tags, folder, status, the linked event and the local-only
// lock. The search box searches titles, participants and the stored summaries, and the matching
// summary line shows under the row with the words marked. A recording opens in the recording view
// (RecordingSheet.tsx) where the theme puts a detail: a pane on the right, a pane below, a card or
// a sheet. "New recording" records in the browser (NewRecording.tsx). Without a Speakr account
// the app says how to connect one. The keys of the `recordings` scope work here (lib/shortcuts.ts).

import { ModalDrawer } from '../../components/ModalDrawer'
import { AudioLines, CalendarDays, CalendarX2, Clock, ExternalLink, Folder, GitMerge, Inbox, Lock, Menu, Mic, MoreHorizontal, Plug, RefreshCw, Search, Split, Tag as TagIcon, Users, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { Avatar, Skeleton } from '../../components/bits'
import { ConfirmDialog, Dialog } from '../../components/Dialog'
import { MenuItem, MenuSep, Popover } from '../../components/Popover'
import { toast, toastError } from '../../stores/ui'
import { useAppRoute } from '../../hooks/useAppRoute'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { eventPath, recordingsPath, type RecordingFilter } from '../../lib/routes'
import { useThemeDef } from '../../themes/useUITheme'
import { useAppShortcuts } from '../shortcuts/useAppShortcuts'
import { useSpeakrStatus } from '../speakr/api'
import { fmtDuration, recordingPath } from '../speakr/format'
import { RecordingView } from '../speakr/RecordingSheet'
import { dismissMerge, mergeParticipant, refreshParticipants, renameInSpeakr, separateParticipant, useAllRecordings, useParticipants, useRecordingSearch, useSpeakrFolders, useSpeakrTags, type MergeResult, type MergeSuggestion } from './api'
import { applyFilter, counts, filterTitle, groupByDay, highlight, identities, statusKind, statusLabel, timeOf, type Counts, type PersonCount, type RecItem } from './model'
import * as recorder from './recorder'
import { clock, useRecorder } from './recorderStore'
import './recordings.css'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import { t as tr, tx, appLocale } from '../../i18n'
import { clockOpts } from '../../lib/dates'

const key = (r: { connector_id: number; recording_id: number }) => `${r.connector_id}:${r.recording_id}`
const sameFilter = (a: RecordingFilter, b: RecordingFilter) => a.kind === b.kind && ('id' in a ? a.id : '') === ('id' in b ? b.id : '')

export default function RecordingsApp() {
  const status = useSpeakrStatus()
  useDocumentTitle(tr('common.recordings'))
  if (status.isLoading) {
    return (
      <section className="app-frame rx-app" data-testid="recordings-app" aria-label={tr('common.recordings')} aria-busy="true">
        <div className="rx-loading">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={48} style={{ margin: '12px 24px' }} />)}</div>
      </section>
    )
  }
  if (!status.data?.connected) {
    return (
      <section className="app-frame rx-app" data-testid="recordings-app" aria-label={tr('common.recordings')}>
        <div className="rx-empty rx-connect" data-testid="recordings-no-account">
          <span className="rx-empty-icon"><Plug size={36} aria-hidden /></span>
          <h2>{tr('recordings.recordingsApp.connectSpeakr')}</h2>
          <p>{tr('recordings.recordingsApp.recordingsComeFromYourSpeakr')}</p>
          {/* A link to Settings, where speakr.connect is offered (web.settings). */}
          <Link className="btn filled" to="/settings/mail/accounts#speakr">{tr('recordings.recordingsApp.connectSpeakr')}</Link>
          <p className="muted rx-small">{tr('recordings.recordingsApp.settingsMailMailAccountsSpeakr')}</p>
        </div>
      </section>
    )
  }
  return <Body speakrUrl={status.data.connectors[0]?.base_url ?? ''} />
}

// ACT recording.view @web.recordings, ACT recording.search @web.recordings, ACT recording.record @web.recordings
function Body({ speakrUrl }: { speakrUrl: string }) {
  const { recordings: route } = useAppRoute()
  const nav = useNavigate()
  const theme = useThemeDef()
  const narrow = useMediaQuery('(max-width: 760px)')
  const placement = narrow ? 'sheet' : theme.apps.taskDetail
  const open = route?.open ?? null

  // Opening a recording keeps the list it was opened from (its filter and search).
  const [listState, setListState] = useState<{ filter: RecordingFilter; q: string }>({ filter: route?.filter ?? { kind: 'all' }, q: route?.q ?? '' })
  useEffect(() => {
    if (route && !route.open) setListState({ filter: route.filter, q: route.q })
  }, [route])
  const { filter, q } = listState
  const [text, setText] = useState(q)
  useEffect(() => setText(q), [q])
  const [who, setWho] = useState<string[]>([])
  const [drawer, setDrawer] = useState(false)
  const [focused, setFocused] = useState<string | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const all = useAllRecordings(true)
  const search = useRecordingSearch(q, true)
  const folders = useSpeakrFolders(true)
  const tags = useSpeakrTags(true)
  const pending = useRecorder((s) => s.pending)
  const recPhase = useRecorder((s) => s.phase)
  const recElapsed = useRecorder((s) => s.elapsedMs)
  useEffect(() => void recorder.refreshPending(), [])

  // Typing searches after a pause; the URL keeps the search.
  useEffect(() => {
    if (text === q) return
    const t = window.setTimeout(() => nav(recordingsPath(filter, text.trim()), { replace: true }), 250)
    return () => window.clearTimeout(t)
  }, [text, q, filter, nav])

  const now = useMemo(() => new Date(all.dataUpdatedAt || Date.now()), [all.dataUpdatedAt])
  const base = useMemo(() => all.data ?? [], [all.data])
  const c = useMemo(() => counts(base, now), [base, now])
  const source = useMemo(() => (q ? (search.data ?? []) : base), [q, search.data, base])
  const shown = useMemo(() => applyFilter(source, filter, now, who), [source, filter, now, who])
  const groups = useMemo(() => groupByDay(shown, now), [shown, now])
  const order = useMemo(() => groups.flatMap((g) => g.items.map(key)), [groups])

  const openRow = (r: RecItem) => nav(recordingPath(r.connector_id, r.recording_id))
  const closePane = () => nav(recordingsPath(filter, q))
  const go = (f: RecordingFilter) => {
    setDrawer(false)
    nav(recordingsPath(f, q))
  }

  // ACT recording.record
  const newRecording = () => {
    useRecorder.getState().open()
  }

  const move = (d: 1 | -1) => {
    if (!order.length) return
    const cur = focused ?? (open ? `${open.cid}:${open.rid}` : null)
    const i = cur ? order.indexOf(cur) : -1
    const next = order[Math.max(0, Math.min(order.length - 1, i < 0 ? 0 : i + d))]
    setFocused(next)
    listRef.current?.querySelector(`[data-key="${next}"]`)?.scrollIntoView({ block: 'nearest' })
    // With the pane showing, the pane follows the cursor.
    if (open && (placement === 'pane' || placement === 'below')) {
      const [cid, rid] = next.split(':').map(Number)
      nav(recordingPath(cid, rid), { replace: true })
    }
  }
  useAppShortcuts('recordings', {
    'recordings.next': () => move(1),
    'recordings.prev': () => move(-1),
    'recordings.open': () => {
      const r = shown.find((x) => key(x) === focused)
      if (!r) return false
      openRow(r)
    },
    'recordings.search': () => searchRef.current?.focus(),
    'recordings.record': () => newRecording(),
    'recordings.escape': () => {
      if (open) closePane()
      else if (q || text) {
        setText('')
        nav(recordingsPath(filter, ''))
      } else if (who.length) setWho([])
      else return false
    },
  })

  const loading = all.isLoading || (!!q && search.isLoading)
  const error = (q ? search.error : null) ?? all.error
  const title = filterTitle(filter)
  const pane = open ? (
    <div className={`rx-pane rx-pane-${placement}`} data-testid="recording-pane">
      <RecordingView key={`${open.cid}:${open.rid}:${open.t}`} cid={open.cid} rid={open.rid} t={open.t} onClose={closePane} />
    </div>
  ) : null

  const sidebar = (
    <Sidebar filter={filter} c={c} folderList={folders.data?.folders.map((f) => f.name) ?? []} tagList={tags.data?.tags.map((t) => t.name) ?? []}
      loading={all.isLoading} who={who} onWho={setWho} onGo={go} speakrUrl={speakrUrl} />
  )

  return (
    <section
      className={`app-frame rx-app rx-theme-${theme.id} rx-detail-${placement}${narrow ? ' rx-narrow' : ''}${open ? ' rx-has-pane' : ''}`}
      data-testid="recordings-app"
      aria-label={tr('common.recordings')}
    >
      {!narrow && sidebar}
      {narrow && drawer && <ModalDrawer className="rx-drawer" scrimClass="rx-drawer-scrim" label={tr('recordings.recordingsApp.recordingsMenu')} onClose={() => setDrawer(false)}>{sidebar}</ModalDrawer>}
      <div className="rx-center">
        <div className="rx-main-col">
          <header className="rx-head">
            {narrow && <button type="button" className="icon-btn" aria-label={tr('recordings.recordingsApp.recordingsMenu')} onClick={() => setDrawer(true)}><Menu /></button>}
            <h1>{q ? tr('common.search') : title}</h1>
            <span className="rx-count" aria-live="polite">{loading ? '' : q ? tr('recordings.recordingsApp.shownOfTotal', { shown: shown.length, total: source.length }) : shown.length}</span>
            <span className="spacer" />
            <label className="rx-search">
              <Search size={16} aria-hidden />
              <input ref={searchRef} type="search" placeholder={tr('recordings.recordingsApp.searchTitlesPeopleAndSummaries')} aria-label={tr('common.searchRecordings')} value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') {
                    e.preventDefault()
                    if (text) setText('')
                    else (e.target as HTMLInputElement).blur()
                  } else if (e.key === 'ArrowDown') {
                    e.preventDefault()
                    ;(e.target as HTMLInputElement).blur()
                    move(1)
                  }
                }} />
              {text && <button type="button" className="icon-btn sm" aria-label={tr('recordings.recordingsApp.clearTheSearch')} onClick={() => { setText(''); nav(recordingsPath(filter, '')) }}><X size={14} /></button>}
            </label>
            <button type="button" className="btn filled rx-new-btn" onClick={newRecording} data-tip={tr('recordings.recordingsApp.newRecordingN')}>
              {recPhase === 'recording' || recPhase === 'paused' ? <><span className="rx-dot" aria-hidden /> {clock(recElapsed)}</> : <><Mic size={16} aria-hidden /> <span className="rx-new-label">{tr('common.newRecording')}</span></>}
            </button>
          </header>
          {(who.length > 0 || (q && filter.kind !== 'all')) && (
            <div className="rx-active-filters" aria-label={tr('recordings.recordingsApp.filters')}>
              {q && filter.kind !== 'all' && <button type="button" className="chip" onClick={() => go({ kind: 'all' })}>{tr('recordings.recordingsApp.in', { title })} <X size={12} aria-label={tr('recordings.recordingsApp.searchEverything')} /></button>}
              {who.map((w) => {
                const label = c.people.find((p) => p.key === w)?.label ?? w.replace(/^(name|email):/, '')
                return <button key={w} type="button" className="chip" onClick={() => setWho(who.filter((x) => x !== w))}><Users size={14} aria-hidden /> {label} <X size={12} aria-label={tr('recordings.recordingsApp.remove', { label })} /></button>
              })}
            </div>
          )}
          {pending.length > 0 && recPhase === 'idle' && <PendingBanner />}
          {error && (
            <div className="rx-banner error" role="alert">
              {tr('recordings.recordingsApp.recordingsCouldNotBeLoaded', { message: (error as Error).message })}
              <button type="button" className="btn text sm" onClick={() => void (q ? search.refetch() : all.refetch())}><RefreshCw size={14} aria-hidden />{' '}{tr('common.tryAgain')}</button>
            </div>
          )}
          <div className="rx-list" ref={listRef} role="list" aria-label={q ? tr('recordings.recordingsApp.recordingsMatching', { q }) : title} aria-busy={loading}>
            {loading ? (
              [0, 1, 2, 3, 4].map((i) => <div key={i} className="rx-skel"><Skeleton width={56} height={14} /><Skeleton height={14} style={{ width: `${70 - i * 9}%` }} /></div>)
            ) : !shown.length ? (
              <Empty q={q} filter={filter} who={who} total={base.length} onNew={newRecording} onAll={() => { setWho([]); go({ kind: 'all' }) }} />
            ) : (
              groups.map((g) => (
                <div key={g.key} className="rx-group" role="presentation">
                  <div className="rx-day" role="heading" aria-level={2}>
                    <span>{g.title}</span>
                    <span className="rx-day-count">{g.items.length}</span>
                  </div>
                  {g.items.map((r) => (
                    <Row key={key(r)} r={r} q={q} active={!!open && open.cid === r.connector_id && open.rid === r.recording_id} focused={focused === key(r)}
                      onOpen={() => { setFocused(key(r)); openRow(r) }} onPerson={(n) => setWho(who.includes(n) ? who : [...who, n])} />
                  ))}
                </div>
              ))
            )}
          </div>
        </div>
        {pane && (placement === 'pane' || placement === 'below') && pane}
      </div>
      {pane && (placement === 'card' || placement === 'sheet') && (
        <div className={`rx-overlay rx-overlay-${placement}`} onMouseDown={(e) => e.target === e.currentTarget && closePane()}>
          {pane}
        </div>
      )}
    </section>
  )
}

function NavItem({ active, icon, label, count, onClick, testid, dim }: { active: boolean; icon: ReactNode; label: string; count?: number; onClick: () => void; testid?: string; dim?: boolean }) {
  return (
    <button type="button" className={`rx-nav${active ? ' active' : ''}${dim ? ' dim' : ''}`} aria-current={active ? 'page' : undefined} onClick={onClick} data-testid={testid}>
      <span className="rx-nav-icon" aria-hidden>{icon}</span>
      <span className="rx-nav-label">{label}</span>
      {count !== undefined && <span className="rx-nav-count">{count}</span>}
    </button>
  )
}

function Sidebar({ filter, c, folderList, tagList, loading, who, onWho, onGo, speakrUrl }: {
  filter: RecordingFilter; c: Counts; folderList: string[]; tagList: string[]; loading: boolean; who: string[]
  onWho: (w: string[]) => void; onGo: (f: RecordingFilter) => void; speakrUrl: string
}) {
  const [morePeople, setMorePeople] = useState(false)
  const n = (v: number) => (loading ? undefined : v)
  // Speakr's folders and tags first (also the empty ones), then any the recordings name that Speakr no longer lists.
  const folderNames = [...new Set([...folderList, ...c.folders.keys()])]
  const tagNames = [...new Set([...tagList, ...c.tags.keys()])]
  const people = morePeople ? c.people.slice(0, 40) : c.people.slice(0, 8)
  return (
    <nav className="rx-side" aria-label={tr('common.recordings')} data-testid="recordings-sidebar">
      <div className="rx-side-group">
        <NavItem active={sameFilter(filter, { kind: 'all' })} icon={<AudioLines />} label={tr('common.all')} count={n(c.all)} onClick={() => onGo({ kind: 'all' })} />
        <NavItem active={sameFilter(filter, { kind: 'recent' })} icon={<Clock />} label={tr('common.recent')} count={n(c.recent)} onClick={() => onGo({ kind: 'recent' })} />
        <NavItem active={sameFilter(filter, { kind: 'linked' })} icon={<CalendarDays />} label={tr('recordings.recordingsApp.linkedToEvents')} count={n(c.linked)} onClick={() => onGo({ kind: 'linked' })} />
        <NavItem active={sameFilter(filter, { kind: 'unlinked' })} icon={<CalendarX2 />} label={tr('common.notLinked')} count={n(c.unlinked)} onClick={() => onGo({ kind: 'unlinked' })} />
      </div>
      {folderNames.length > 0 && (
        <div className="rx-side-group" role="group" aria-label={tr('common.folders')}>
          <div className="rx-side-head">{tr('common.folders')}</div>
          {folderNames.map((f) => (
            <NavItem key={f} active={sameFilter(filter, { kind: 'folder', id: f })} icon={<Folder />} label={f} count={n(c.folders.get(f) ?? 0)} dim={!c.folders.get(f)} onClick={() => onGo({ kind: 'folder', id: f })} />
          ))}
        </div>
      )}
      {tagNames.length > 0 && (
        <div className="rx-side-group" role="group" aria-label={tr('common.tags')}>
          <div className="rx-side-head">{tr('common.tags')}</div>
          {tagNames.map((t) => (
            <NavItem key={t} active={sameFilter(filter, { kind: 'tag', id: t })} icon={<TagIcon />} label={t} count={n(c.tags.get(t) ?? 0)} dim={!c.tags.get(t)} onClick={() => onGo({ kind: 'tag', id: t })} />
          ))}
        </div>
      )}
      {c.people.length > 0 && (
        <div className="rx-side-group" role="group" aria-label={tr('common.participants')}>
          <div className="rx-side-head">{tr('common.participants')}</div>
          <MergeSuggestions people={c.people} />
          {people.map((p) => {
            const on = who.includes(p.key)
            return (
              <PersonRow key={p.key} p={p} on={on} all={c.people}
                onToggle={() => onWho(on ? who.filter((w) => w !== p.key) : [...who, p.key])} />
            )
          })}
          {c.people.length > 8 && (
            <button type="button" className="btn text sm rx-more" onClick={() => setMorePeople(!morePeople)}>{morePeople ? tr('recordings.recordingsApp.fewer') : tr('recordings.recordingsApp.more', { value: c.people.length - 8 })}</button>
          )}
        </div>
      )}
      {speakrUrl && (
        <a className="rx-side-link" href={speakrUrl} target="_blank" rel="noreferrer"><ExternalLink size={14} aria-hidden />{' '}{tr('recordings.recordingsApp.openSpeakr')}</a>
      )}
    </nav>
  )
}


/** After a merge: the toast, with Also rename in Speakr when the connector's write-back is on. */
function merged(qc: ReturnType<typeof useQueryClient>, res: MergeResult, what: string) {
  refreshParticipants(qc)
  if (!res.merged.length) {
    toast({ message: res.skipped.length ? tr('recordings.recordingsApp.alreadyHasAPersonFrom', { skipped: res.skipped.join(', ') }) : tr('recordings.recordingsApp.nothingToMerge') })
    return
  }
  const cid = res.write_back[0]
  const names = res.merged
  toast({
    message: tr('recordings.recordingsApp.areOnePersonNow', { what }),
    ...(cid !== undefined && res.into_label && res.into_label !== 'You'
      ? {
          actionLabel: tr('recordings.recordingsApp.alsoRenameInSpeakr'),
          onAction: () => {
            Promise.all(names.map((n) => renameInSpeakr(cid, n, res.into_label)))
              .then((r) => { refreshParticipants(qc); toast({ message: tr('recordings.recordingsApp.renamedInRecordingsInSpeakr', { count: r.reduce((a, x) => a + x.renamed, 0) }) }) })
              .catch((e) => toastError(e))
          },
        }
      : {}),
  })
}

/** Suggested merges: a first name into a full name (Merge Ross into Ross Turner?). */
function MergeSuggestions({ people }: { people: PersonCount[] }) {
  const ov = useParticipants(true)
  const qc = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const list = (ov.data?.suggestions ?? []).filter((s) => people.some((p) => p.key === s.into_key)).slice(0, 2)
  if (!list.length) return null
  const id = (s: MergeSuggestion) => `${s.connector_id}:${s.name}:${s.into}`
  // ACT participant.merge @web.recordings (a suggestion's Merge and Dismiss)
  const merge = (s: MergeSuggestion) => {
    setBusy(id(s))
    mergeParticipant({ connector_id: s.connector_id, name: s.name, into: s.into })
      .then((res) => merged(qc, res, tr('recordings.recordingsApp.and', { label: s.label, intoLabel: s.into_label })))
      .catch((e) => toastError(e))
      .finally(() => setBusy(null))
  }
  const dismiss = (s: MergeSuggestion) => {
    setBusy(id(s))
    dismissMerge(s).then(() => refreshParticipants(qc)).catch((e) => toastError(e)).finally(() => setBusy(null))
  }
  return (
    <div className="rx-merge-sugs">
      {list.map((s) => (
        <div key={id(s)} className="rx-merge-sug" data-testid="participant-suggestion">
          <span className="rx-merge-q">{tx('recordings.recordingsApp.mergeInto', { label: s.label, intoLabel: s.into_label }, { b: (c) => <b>{c}</b> })}</span>
          <span className="rx-merge-acts">
            <button type="button" className="btn tonal sm" disabled={busy === id(s)} onClick={() => merge(s)}>{tr('recordings.recordingsApp.merge')}</button>
            <button type="button" className="btn text sm" disabled={busy === id(s)} onClick={() => dismiss(s)}>{tr('common.dismiss')}</button>
          </span>
        </div>
      ))}
    </div>
  )
}

/** A person in the Participants list: the filter toggle, and a menu with Same person as… and Separate. */
function PersonRow({ p, on, all, onToggle }: { p: PersonCount; on: boolean; all: PersonCount[]; onToggle: () => void }) {
  const qc = useQueryClient()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [picking, setPicking] = useState(false)
  const joined = p.names.filter((n) => n.how === 'alias' || n.how === 'me_first')
  const tip = p.names.length > 1 ? `${p.label}: ${p.names.map((n) => n.name).join(', ')}` : undefined
  // ACT participant.merge (Separate)
  const separate = (name: string) => {
    setAnchor(null)
    separateParticipant(name)
      .then(() => { refreshParticipants(qc); toast({ message: tr('recordings.recordingsApp.isShownOnItsOwn', { itemName: name }) }) })
      .catch((e) => toastError(e))
  }
  // ACT participant.merge (Same person as…)
  const mergeInto = (to: PersonCount) => {
    setPicking(false)
    mergeParticipant({ key: p.key, into: to.key })
      .then((res) => merged(qc, res, tr('recordings.recordingsApp.and2', { label: p.label, toLabel: to.label })))
      .catch((e) => toastError(e))
  }
  return (
    <div className="rx-person-row" data-testid="participant-row" data-key={p.key}>
      <button type="button" className={`rx-nav rx-person-nav${on ? ' active' : ''}`} aria-pressed={on} onClick={onToggle} data-tip={tip}>
        <Avatar who={{ name: p.me ? '' : p.label, email: p.email ?? '' }} size="xs" />
        <span className="rx-nav-label">{p.label}</span>
        <span className="rx-nav-count">{p.count}</span>
      </button>
      {(!p.me || joined.length > 0) && (
        <button type="button" className="icon-btn xs rx-person-more" aria-label={tr('recordings.recordingsApp.mergeOrSeparate')} data-tip={tr('recordings.recordingsApp.mergeOrSeparate')} aria-haspopup="menu" aria-expanded={!!anchor}
          onClick={(e) => setAnchor(e.currentTarget)}><MoreHorizontal size={14} /></button>
      )}
      <Popover anchor={anchor} open={!!anchor} onClose={() => setAnchor(null)} label={tr('recordings.recordingsApp.optionsFor', { label: p.label })} placement="bottom-end" minWidth={220}>
        {!p.me && <MenuItem icon={<GitMerge size={16} aria-hidden />} onSelect={() => { setAnchor(null); setPicking(true) }}>{tr('recordings.recordingsApp.samePersonAs')}</MenuItem>}
        {!p.me && joined.length > 0 && <MenuSep />}
        {joined.map((n) => (
          <MenuItem key={n.name} icon={<Split size={16} aria-hidden />} onSelect={() => separate(n.name)}>{tr('recordings.recordingsApp.separate', { itemName: n.name })}</MenuItem>
        ))}
      </Popover>
      {picking && <PersonPicker from={p} people={all} onClose={() => setPicking(false)} onPick={mergeInto} />}
    </div>
  )
}

function PersonPicker({ from, people, onPick, onClose }: { from: PersonCount; people: PersonCount[]; onPick: (p: PersonCount) => void; onClose: () => void }) {
  const [q, setQ] = useState('')
  const t = q.trim().toLowerCase()
  const rows = people.filter((x) => x.key !== from.key && (!t || x.label.toLowerCase().includes(t) || x.names.some((n) => n.name.toLowerCase().includes(t)))).slice(0, 30)
  return (
    <Dialog open onClose={onClose} title={tr('recordings.recordingsApp.isTheSamePersonAs', { fromLabel: from.label })} initialFocus="input">
      <input className="input rx-pick-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr('recordings.recordingsApp.searchParticipants')} aria-label={tr('recordings.recordingsApp.searchParticipants')} />
      <p className="muted rx-small">{tr('recordings.recordingsApp.everyRecordingWithThenCounts', { map: from.names.map((n) => n.name).join(' or ') })}</p>
      <ul className="rx-pick-list" aria-label={tr('common.participants')}>
        {rows.map((x) => (
          <li key={x.key}>
            <button type="button" className="rx-pick" onClick={() => onPick(x)}>
              <Avatar who={{ name: x.me ? '' : x.label, email: x.email ?? '' }} size="xs" />
              <span className="rx-nav-label">{x.label}</span>
              <span className="rx-nav-count">{x.count}</span>
            </button>
          </li>
        ))}
      </ul>
      {!rows.length && <p className="muted">{tr('recordings.recordingsApp.nobodyMatches')}</p>}
    </Dialog>
  )
}

function People({ r }: { r: RecItem }) {
  const list = identities(r).map((p) => ({ ...p, name: p.label }))
  if (!list.length) return null
  const shown = list.slice(0, 3)
  return (
    <span className="rx-avatars" aria-label={tr('recordings.recordingsApp.with', { map: list.map((p) => p.name).join(', ') })} data-tip={list.map((p) => p.name).join(', ')}>
      {shown.map((p) => <Avatar key={p.key} who={{ name: p.me ? '' : p.name, email: p.email ?? '' }} size="xs" />)}
      {list.length > 3 && <span className="rx-avatar-more">+{list.length - 3}</span>}
    </span>
  )
}

function Row({ r, q, active, focused, onOpen, onPerson }: { r: RecItem; q: string; active: boolean; focused: boolean; onOpen: () => void; onPerson: (key: string) => void }) {
  const nav = useNavigate()
  const kind = statusKind(r.status)
  const ev = r.event_link
  const meta = [timeOf(r), fmtDuration(r.duration_s)].filter(Boolean)
  const ids = identities(r)
  return (
    <div role="listitem" className={`rx-row${active ? ' active' : ''}${focused ? ' focused' : ''}`} data-key={key(r)} data-testid="recording-row">
      <button type="button" className="rx-row-hit" onClick={onOpen} aria-label={tr('recordings.recordingsApp.open', { title: r.title || 'recording' })} />
      <div className="rx-when">
        <span className="rx-time">{meta[0] ?? ''}</span>
        <span className="rx-dur">{meta[1] ?? ''}</span>
      </div>
      <div className="rx-body">
        <div className="rx-line1">
          <span className="rx-title">{r.title || tr('common.untitledRecording')}</span>
          {kind !== 'done' && <span className={`rx-status ${kind}`}>{statusLabel(r.status)}</span>}
          {r.local_only && <span className="rx-lock" data-tip={tr('recordings.recordingsApp.localOnlyOnlyLocalModels')} aria-label={tr('common.localOnly2')}><Lock size={13} /></span>}
        </div>
        <div className="rx-line2">
          {ev && (
            <button type="button" className={`rx-event ${ev.state}`} onClick={() => nav(eventPath(ev.target, ev.occurrence ?? ''))}
              data-tip={ev.state === 'suggested' ? tr('recordings.recordingsApp.suggestedLinkOpenTheEvent') : tr('recordings.recordingsApp.openTheEvent')}>
              <CalendarDays size={13} aria-hidden />
              {ev.state === 'suggested' && <span className="rx-event-q">{tr('recordings.recordingsApp.suggested')}</span>}
              <span className="rx-event-title">{ev.title || tr('common.event')}</span>
            </button>
          )}
          {ids.length > 0 && (
            <span className="rx-names">
              {ids.slice(0, 4).map((p, i) => (
                <span key={p.key}>{i > 0 && ', '}<button type="button" className="rx-name" onClick={() => onPerson(p.key)} data-tip={tr('recordings.recordingsApp.onlyRecordingsWith', { value: p.me ? 'you' : p.label })}>{p.label}</button></span>
              ))}
              {ids.length > 4 && <span className="muted"> +{ids.length - 4}</span>}
            </span>
          )}
          {r.folder && <span className="rx-folder"><Folder size={12} aria-hidden /> {r.folder}</span>}
          {r.tags.map((t) => <span key={t} className="rx-tag">{t}</span>)}
        </div>
        {q && r.summary_match && (
          <div className="rx-match" data-testid="summary-match">
            {highlight(r.summary_match, q).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
          </div>
        )}
      </div>
      <People r={r} />
    </div>
  )
}

function Empty({ q, filter, who, total, onNew, onAll }: { q: string; filter: RecordingFilter; who: string[]; total: number; onNew: () => void; onAll: () => void }) {
  if (!total && !q) {
    return (
      <div className="rx-empty" data-testid="recordings-empty">
        <span className="rx-empty-icon"><AudioLines size={36} aria-hidden /></span>
        <h2>{tr('recordings.recordingsApp.noRecordingsYet')}</h2>
        <p>{tr('recordings.recordingsApp.recordingsYouMakeInSpeakr')}</p>
        <button type="button" className="btn filled" onClick={onNew}><Mic size={16} aria-hidden />{' '}{tr('common.newRecording')}</button>
      </div>
    )
  }
  const narrowed = filter.kind !== 'all' || who.length > 0
  return (
    <div className="rx-empty small" data-testid="recordings-empty">
      <span className="rx-empty-icon"><Inbox size={28} aria-hidden /></span>
      <h2>{q ? tr('recordings.recordingsApp.nothingMatches', { q }) : tr('recordings.recordingsApp.noRecordingsHere')}</h2>
      <p>{q ? tr('recordings.recordingsApp.theSearchLooksAtTitles') : filterHint(filter)}</p>
      {narrowed && <button type="button" className="btn tonal" onClick={onAll}>{tr('recordings.recordingsApp.showAllRecordings')}</button>}
    </div>
  )
}

function filterHint(f: RecordingFilter): string {
  if (f.kind === 'recent') return tr('recordings.recordingsApp.nothingWasRecordedInThe')
  if (f.kind === 'linked') return tr('recordings.recordingsApp.aRecordingIsLinkedTo')
  if (f.kind === 'unlinked') return tr('recordings.recordingsApp.everyRecordingIsLinkedTo')
  return tr('recordings.recordingsApp.noRecordingMatchesTheseFilters')
}

/** A recording a closed tab left on this device, offered for upload. */
function PendingBanner() {
  const pending = useRecorder((s) => s.pending)
  const [asking, setAsking] = useState(false)
  const p = pending[0]
  if (!p) return null
  const when = new Date(p.startedAt).toLocaleString(appLocale(), { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', ...clockOpts() })
  return (
    <div className="rx-banner" role="status" data-testid="pending-recording">
      <Mic size={16} aria-hidden />
      <span className="grow">
        {tr('recordings.recordingsApp.aRecordingFromWasNot', { when, clock: clock(p.durationMs), value: p.title ? `, “${p.title}”` : '', value2: pending.length > 1 && tr('recordings.recordingsApp.more2', { value: pending.length - 1 }) })}
      </span>
      {/* ACT recording.record */}
      <button type="button" className="btn tonal sm" onClick={() => void recorder.recover(p.id)}>{tr('recordings.recordingsApp.upload')}</button>
      <button type="button" className="btn text sm" onClick={() => setAsking(true)}>{tr('common.discard')}</button>
      <ConfirmDialog open={asking} title={tr('recordings.recordingsApp.discardThisRecording')} danger confirmLabel={tr('common.discard')} onClose={() => setAsking(false)}
        body={tr('recordings.recordingsApp.theAudioAndItsNotes')}
        onConfirm={() => { setAsking(false); void recorder.forget(p.id) }} />
    </div>
  )
}

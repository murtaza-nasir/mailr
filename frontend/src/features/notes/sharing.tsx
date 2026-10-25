// Sharing notes, lists and tables (notes/sharing.py): the people dialog, the
// notice of new shares in the left column, and the lines that say who else sees an item. The model
// and the wording follow calendar sharing (features/calendar/ShareDialog.tsx): view, edit, or edit
// and share; mailr users and mailr groups; the one instance policy; only the signed-in user shares.

import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { LogOut, UserPlus, Users, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { request } from '../../api/client'
import { Skeleton } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { appNavigate } from '../../lib/appNav'
import { notePath, tableRoutePath } from '../../lib/routes'
import { toast, toastError } from '../../stores/ui'
import { offerUndo } from './api'
import type { AccessLevel, ShareLevel, SharingSummary, Undo } from './types'
import { t, appLocale } from '../../i18n'

export type ShareKind = 'note' | 'table'

export const SHARE_LEVELS: { id: ShareLevel; label: string; desc: string }[] = [
  { id: 'view', label: t('common.view'), desc: t('notes.sharing.seesItChangesNothing') },
  { id: 'edit', label: t('common.edit2'), desc: t('notes.sharing.addsChecksAndChangesWhat') },
  { id: 'share', label: t('notes.sharing.editAndShare'), desc: t('notes.sharing.editsAndSharesItWith') },
]

const levelName = (l: string) => SHARE_LEVELS.find((x) => x.id === l)?.label ?? l

export interface SharePerson {
  kind: 'user'
  id: number
  name: string
  email: string
  level: ShareLevel
  disabled: boolean
  you: boolean
  outside_policy: boolean
}

export interface ShareGroup {
  kind: 'group'
  name: string
  level: ShareLevel
  members: number
  outside_policy: boolean
}

export interface ShareState {
  id: string
  kind: ShareKind
  noun: 'note' | 'list' | 'table'
  name: string
  level: AccessLevel
  own: boolean
  can_share: boolean
  policy: 'anyone' | 'groups' | 'nobody'
  owner: { kind: 'user'; id: number; name: string; email: string } | { kind: 'group'; name: string }
  created_by: { id: number; name: string; email: string } | null
  people: SharePerson[]
  groups: ShareGroup[]
  my_groups: string[]
  can_move_in: boolean
  can_move_out: boolean
  can_leave: boolean
  /** The people in the viewer's groups, plus (on a search of `search_min` characters or more) up to
   *  eight others who match it. The server never lists every account. */
  candidates: { id: number; name: string; email: string }[]
  search_min?: number
  /** The candidates who share a group with the viewer: "Share with Cleo" offers them (quickShare.tsx). */
  household?: number[]
  group_candidates: string[]
  /** People who removed it from their notes (owner and "edit and share" only): who and when. */
  left?: { kind: 'user'; id: number; name: string; email: string; left_at: string; via_group: boolean }[]
  max: number
}

export interface ShareNotice {
  path: string
  kind: ShareKind
  id: string
  name: string
  noun: string
  by: string
  at: string
  /** A local-only change by someone else on the user's own item; a new share when absent. */
  what?: 'local_only'
  on?: boolean
}

const base = (kind: ShareKind, id: string) => `/${kind}s/${encodeURIComponent(id)}`
export const shareKey = (kind: ShareKind, id: string) => ['notes', 'shares', kind, id] as const
export const NOTICES_KEY = ['notes', 'share-notices'] as const

export const shareApi = {
  get: (kind: ShareKind, id: string, q?: string) =>
    request<ShareState>(`${base(kind, id)}/shares${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  // ACT note.share @web.notes @web.note, ACT table.share @web.table @web.notes
  // `email`: the address a people search showed, for someone outside your groups.
  set: (kind: ShareKind, id: string, principal: string, level: ShareLevel, email?: string) =>
    request<ShareState>(`${base(kind, id)}/shares/${encodeURIComponent(principal)}`, { method: 'PUT', body: email ? { level, email } : { level } }),
  // ACT note.unshare @web.notes @web.note, ACT table.unshare @web.table @web.notes
  stop: (kind: ShareKind, id: string, principal: string) =>
    request<ShareState>(`${base(kind, id)}/shares/${encodeURIComponent(principal)}`, { method: 'DELETE' }),
  // ACT note.move_group @web.note, ACT table.move_group @web.table
  move: (kind: ShareKind, id: string, group: string | null) =>
    request<ShareState>(`${base(kind, id)}/move-to-group`, { method: 'POST', body: { group } }),
}

/** note.leave / table.leave: the item leaves this viewer's notes; the toast offers "Undo" (add back).
 *  ACT note.leave @web.notes @web.note, ACT table.leave @web.notes @web.table */
export async function leaveShared(qc: QueryClient, kind: ShareKind, id: string): Promise<boolean> {
  try {
    const r = await request<{ undo?: Undo | null }>(`${base(kind, id)}/shares/me`, { method: 'DELETE' })
    offerUndo(qc, r.undo ?? null, t('notes.sharing.removedFromYourNotes'))
    void qc.invalidateQueries({ queryKey: ['notes'] })
    void qc.invalidateQueries({ queryKey: ['tables'] })
    return true
  } catch (e) {
    toastError(e, t('notes.sharing.couldNotRemoveItFrom'))
    return false
  }
}

/** "Shared by Olive", "In the Household group", "Shared with 2 people", or '' (only the viewer sees it). */
export function sharedLine(x: { level: AccessLevel; owner?: { kind: 'user' | 'group'; name?: string }; sharing?: SharingSummary }): string {
  if (x.owner?.kind === 'group') return t('notes.sharing.inTheGroup', { ownerName: x.owner.name })
  if (x.level !== 'owner') return x.owner?.name ? t('notes.sharing.sharedBy', { ownerName: x.owner.name }) : t('notes.sharing.sharedWithYou')
  const n = x.sharing?.count ?? 0
  if (!n) return ''
  const people = x.sharing?.people ?? []
  if (n === 1 && people[0]?.kind === 'group') return t('notes.sharing.sharedWithTheGroup', { itemName: people[0].name })
  return t('notes.sharing.sharedWith', { n, value: n === 1 ? (people[0]?.kind === 'group' ? 'group' : 'person') : 'people' })
}

/** "View only · shared by Olive". */
export function viewOnlyLine(x: { owner?: { kind: 'user' | 'group'; name?: string } }): string {
  const by = x.owner?.kind === 'group' ? t('notes.sharing.theGroup', { ownerName: x.owner.name }) : x.owner?.name
  return t('notes.sharing.viewOnly', { value: by ? t('notes.sharing.sharedBy2', { by }) : '' })
}

/** Initials for the people on a card ("OD"), at most four. */
export function initials(x: { owner?: { kind: 'user' | 'group'; name?: string }; level: AccessLevel; sharing?: SharingSummary }): string[] {
  const names = [...(x.level !== 'owner' && x.owner?.name ? [x.owner.name] : []), ...(x.sharing?.people ?? []).map((p) => p.name)]
  return [...new Set(names.filter(Boolean))].slice(0, 4).map((n) => n.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase())
}

/** The policy line under the title (only when it narrows the choice). */
export function policyNote(s: Pick<ShareState, 'policy' | 'own' | 'owner' | 'noun' | 'can_share'>): string {
  if (!s.can_share) return ''
  if (s.policy === 'nobody') return t('notes.sharing.sharingIsTurnedOffOn')
  if (s.policy === 'groups') return t('notes.sharing.onlyPeopleInYourGroups')
  return ''
}

function ownerLine(s: ShareState): string {
  if (s.owner.kind === 'group') {
    return t('notes.sharing.thisBelongsToTheGroup', { noun: s.noun, ownerName: s.owner.name, value: s.created_by ? t('notes.sharing.madeIt', { createdByName: s.created_by.name }) : '' })
  }
  return s.own ? t('notes.sharing.youOwnThis', { noun: s.noun }) : t('notes.sharing.ownsThis', { ownerName: s.owner.name, noun: s.noun })
}

/** People matching the search, by name or address. */
function matching<T extends { name: string; email?: string }>(people: T[], q: string, n = 8): T[] {
  const s = q.trim().toLowerCase()
  const hit = s ? people.filter((p) => p.name.toLowerCase().includes(s) || (p.email ?? '').toLowerCase().includes(s)) : people
  return hit.slice(0, n)
}

/** The people dialog: the owner line, the people and groups with their levels (change, stop), a
 *  picker of mailr users and the user's own groups, "Move to group" for the owner, and "Remove from
 *  my notes" for a sharee. */
export function ShareDialog({ kind, id, name, onClose, onLeft }: { kind: ShareKind; id: string; name: string; onClose: () => void; onLeft?: () => void }) {
  const qc = useQueryClient()
  const key = shareKey(kind, id)
  const q = useQuery<ShareState>({ queryKey: key, queryFn: () => shareApi.get(kind, id), staleTime: 0, retry: false })
  const [search, setSearch] = useState('')
  const [level, setLevel] = useState<ShareLevel>('edit')
  const [busy, setBusy] = useState(false)
  const [moveTo, setMoveTo] = useState('')
  const s = q.data
  // People outside the viewer's groups come from a typed search, 250 ms after the last key.
  const [term, setTerm] = useState('')
  useEffect(() => {
    const h = window.setTimeout(() => setTerm(search.trim()), 250)
    return () => window.clearTimeout(h)
  }, [search])
  const minSearch = s?.search_min ?? 3
  const found = useQuery<ShareState>({
    queryKey: [...key, 'search', term], queryFn: () => shareApi.get(kind, id, term),
    enabled: !!s?.can_share && term.length >= minSearch, staleTime: 30_000, retry: false,
  })
  const pool = useMemo(() => {
    const out = [...(s?.candidates ?? [])]
    const have = new Set(out.map((c) => c.id))
    const taken = new Set((s?.people ?? []).map((p) => p.id))
    for (const c of term.length >= minSearch ? found.data?.candidates ?? [] : []) {
      if (!have.has(c.id) && !taken.has(c.id)) { have.add(c.id); out.push(c) }
    }
    return out
  }, [s?.candidates, s?.people, found.data, term, minSearch])
  const shown = useMemo(() => matching(pool, search), [pool, search])
  const groupsShown = useMemo(() => matching((s?.group_candidates ?? []).map((g) => ({ name: g })), search, 5), [s?.group_candidates, search])
  const run = (p: Promise<ShareState>, ok: string) => {
    setBusy(true)
    // A read still on its way (the dialog refetches on open) must not put the old list back.
    void qc.cancelQueries({ queryKey: key })
    p.then((next) => {
      qc.setQueryData(key, next)
      setSearch('')
      qc.removeQueries({ queryKey: [...key, 'search'] })
      void qc.invalidateQueries({ queryKey: ['notes'] })
      void qc.invalidateQueries({ queryKey: ['tables'] })
      toast({ message: ok })
    }, (e) => toastError(e, t('notes.sharing.couldNotChangeWhoSees'))).finally(() => setBusy(false))
  }
  const editable = !!s?.can_share
  return (
    <Dialog open onClose={onClose} title={t('notes.sharing.share', { itemName: s?.name ?? name })}
      footer={<button type="button" className="btn filled" onClick={onClose}>{t('common.done')}</button>}>
      <div className="share-dlg nt-share" data-testid="note-share-dialog">
        {q.isLoading ? (
          <Skeleton height={80} />
        ) : q.isError || !s ? (
          <p className="muted" role="alert">{(q.error as Error | null)?.message ?? t('notes.sharing.thisCannotBeShared')}</p>
        ) : (
          <>
            <p className="muted share-note" data-testid="share-owner">{ownerLine(s)}</p>
            {policyNote(s) && <p className="muted share-note" data-testid="share-policy">{policyNote(s)}</p>}
            {editable && s.policy !== 'nobody' && (
              <div className="share-add">
                <div className="row share-add-row">
                  <input className="input" type="search" placeholder={t('notes.sharing.addPeopleOrGroupsBy')} aria-label={t('notes.sharing.findPeopleOrGroups')}
                    value={search} onChange={(e) => setSearch(e.target.value)} data-testid="share-search" />
                  <select className="input share-level" aria-label={t('notes.sharing.levelForNewPeople')} value={level} onChange={(e) => setLevel(e.target.value as ShareLevel)} data-testid="share-new-level">
                    {SHARE_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                  </select>
                </div>
                <p className="muted share-level-desc">{SHARE_LEVELS.find((l) => l.id === level)?.desc}</p>
                {pool.length === 0 && s.group_candidates.length === 0 && search.trim().length < minSearch ? (
                  <p className="muted" data-testid="share-search-hint">{s.policy === 'groups'
                    ? (s.people.length || s.groups.length ? t('notes.sharing.everyoneYouCanShareWith') : t('notes.sharing.thereIsNobodyYouCan'))
                    : t('notes.sharing.typeANameOrAddress', { count: minSearch })}</p>
                ) : (
                  <ul className="share-cands" aria-label={t('notes.sharing.peopleAndGroupsYouCan')}>
                    {groupsShown.map((g) => (
                      <li key={`g:${g.name}`} className="share-person" data-testid="share-candidate">
                        <span className="share-who"><b><Users size={13} aria-hidden /> {g.name}</b><span className="muted">{t('notes.sharing.everyoneInTheGroup')}</span></span>
                        <button type="button" className="btn tonal sm" disabled={busy} aria-label={t('notes.sharing.shareWithTheGroup', { itemName: g.name })}
                          onClick={() => run(shareApi.set(kind, id, `group:${g.name}`, level), `Shared with the ${g.name} group (${levelName(level).toLowerCase()})`)}>
                          <UserPlus size={14} aria-hidden />{' '}{t('common.add')}
                        </button>
                      </li>
                    ))}
                    {shown.map((p) => (
                      <li key={p.id} className="share-person" data-testid="share-candidate">
                        <span className="share-who"><b>{p.name}</b><span className="muted">{p.email}</span></span>
                        <button type="button" className="btn tonal sm" disabled={busy} aria-label={t('notes.sharing.shareWith', { itemName: p.name })}
                          onClick={() => run(shareApi.set(kind, id, String(p.id), level, p.email), `Shared with ${p.name} (${levelName(level).toLowerCase()})`)}>
                          <UserPlus size={14} aria-hidden />{' '}{t('common.add')}
                        </button>
                      </li>
                    ))}
                    {shown.length === 0 && groupsShown.length === 0 && search.trim().length >= minSearch
                      && !found.isFetching && term === search.trim() && <li className="muted">{t('notes.sharing.nobodyMatches', { search })}</li>}
                    {s.policy === 'anyone' && search.trim().length < minSearch && (
                      <li className="muted" data-testid="share-search-hint">{t('notes.sharing.typeANameOrAddress', { count: minSearch })}</li>
                    )}
                  </ul>
                )}
              </div>
            )}
            <h3 className="share-head">{t('notes.sharing.sharedWith2')}</h3>
            {s.people.length === 0 && s.groups.length === 0 ? (
              <p className="muted" data-testid="share-none">{s.owner.kind === 'group' ? t('notes.sharing.nobodyOutsideTheGroup') : t('notes.sharing.nobodyYetOnlyThis', { value: s.own ? t('notes.sharing.youSee') : t('notes.sharing.andYouSee', { ownerName: s.owner.name }), noun: s.noun })}</p>
            ) : (
              <ul className="share-people" aria-label={t('notes.sharing.peopleWithAccess')}>
                {[...s.groups, ...s.people].map((p) => {
                  const principal = p.kind === 'group' ? `group:${p.name}` : String(p.id)
                  const label = p.kind === 'group' ? t('notes.sharing.theGroup2', { itemName: p.name }) : p.name
                  return (
                    <li key={principal} className="share-person" data-testid="share-person">
                      <span className="share-who">
                        <b>{p.kind === 'group' && <Users size={13} aria-hidden />} {p.name}{p.kind === 'user' && p.you ? t('common.youSuffix') : ''}</b>
                        <span className="muted">
                          {p.kind === 'group' ? t('notes.sharing.members', { count: p.members }) : p.email}
                          {p.kind === 'user' && p.disabled ? t('notes.sharing.accountDisabled') : ''}
                          {p.outside_policy && !(p.kind === 'user' && p.disabled) && editable ? t('notes.sharing.outsideTheSharingPolicy') : ''}
                        </span>
                      </span>
                      {editable && !(p.kind === 'user' && p.you) ? (
                        <>
                          <select className="input share-level" aria-label={t('notes.sharing.levelFor', { label })} value={p.level} disabled={busy || s.policy === 'nobody'}
                            onChange={(e) => {
                              const l = e.target.value as ShareLevel
                              run(shareApi.set(kind, id, principal, l), `${p.kind === 'group' ? `The ${p.name} group` : p.name} can now: ${levelName(l).toLowerCase()}`)
                            }}>
                            {SHARE_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                          </select>
                          <button type="button" className="icon-btn sm" aria-label={t('notes.sharing.stopSharingWith', { label })} data-tip={t('notes.sharing.stopSharing')} disabled={busy}
                            onClick={() => run(shareApi.stop(kind, id, principal), `Stopped sharing with ${label}`)}>
                            <X size={16} />
                          </button>
                        </>
                      ) : (
                        <span className="muted nt-small">{levelName(p.level)}</span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
            {!!s.left?.length && (
              <div data-testid="share-left">
                <h3 className="share-head">{t('notes.sharing.removedItFromTheirNotes')}</h3>
                <ul className="share-people" aria-label={t('notes.sharing.peopleWhoRemovedIt')}>
                  {s.left.map((p) => (
                    <li key={p.id} className="share-person">
                      <span className="share-who">
                        <b>{p.name}</b>
                        <span className="muted">{p.email} · {new Date(p.left_at).toLocaleDateString(appLocale(), { day: 'numeric', month: 'short' })}{p.via_group ? t('notes.sharing.hadItThroughAGroup') : ''}</span>
                      </span>
                      {editable && s.policy !== 'nobody' && (
                        <button type="button" className="btn text sm" disabled={busy} aria-label={t('notes.sharing.shareAgainWith', { itemName: p.name })}
                          onClick={() => run(shareApi.set(kind, id, String(p.id), 'view'), `Shared with ${p.name} again (view)`)}>{t('notes.sharing.shareAgain')}</button>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="muted nt-small">{t('notes.sharing.theyNoLongerSeeIt')}</p>
              </div>
            )}
            {s.can_move_in && (
              <div className="nt-share-move">
                <h3 className="share-head">{t('notes.sharing.moveToGroup')}</h3>
                <p className="muted share-note">{t('notes.sharing.everyoneInTheGroupEdits')}</p>
                <div className="row share-add-row">
                  <select className="input" aria-label={t('common.group')} value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
                    <option value="">{t('notes.sharing.chooseAGroup')}</option>
                    {s.my_groups.map((g) => <option key={g} value={g}>{g}</option>)}
                  </select>
                  <button type="button" className="btn tonal sm" disabled={!moveTo || busy}
                    onClick={() => run(shareApi.move(kind, id, moveTo), `Moved to the ${moveTo} group`)}>{t('common.move')}</button>
                </div>
              </div>
            )}
            {s.can_move_out && (
              <div className="nt-share-move">
                <button type="button" className="btn text sm" disabled={busy} data-testid="share-move-out"
                  onClick={() => run(shareApi.move(kind, id, null), `Moved out of the ${s.owner.kind === 'group' ? s.owner.name : ''} group`)}>
                  {t('notes.sharing.moveOutOfTheGroup')}
                </button>
                <span className="muted nt-small">{t('notes.sharing.itBecomesAgain', { value: s.created_by && s.created_by.name ? `${s.created_by.name}'s` : 'yours' })}</span>
              </div>
            )}
            {s.can_leave && (
              <div className="nt-share-move">
                <button type="button" className="btn text sm" disabled={busy} data-testid="share-leave" onClick={() => {
                  void leaveShared(qc, kind, id).then((ok) => {
                    if (ok) {
                      onClose()
                      onLeft?.()
                    }
                  })
                }}><LogOut size={14} aria-hidden />{' '}{t('notes.sharing.removeFromMyNotes')}</button>
                <span className="muted nt-small">{t('notes.sharing.itStaysFor', { value: s.owner.kind === 'user' ? s.owner.name : t('notes.sharing.theOthers') })}</span>
              </div>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}

// ---------------------------------------------------------------- notices

/** Notes and tables people shared with this user since the last dismissal.
 *  ACT note.share_notices @web.notes */
export function useShareNotices() {
  return useQuery<ShareNotice[]>({
    queryKey: NOTICES_KEY,
    queryFn: async ({ signal }) => (await request<{ notices: ShareNotice[] }>('/notes/share-notices', { signal })).notices ?? [],
    staleTime: 30_000,
    retry: false,
  })
}

/** "Olive shared “Groceries” with you." in the left column, until dismissed (as calendars do). */
export function ShareNotices() {
  const qc = useQueryClient()
  const notes = useShareNotices().data ?? []
  if (!notes.length) return null
  const dismiss = () =>
    request('/notes/share-notices', { method: 'DELETE' }).then(() => void qc.invalidateQueries({ queryKey: NOTICES_KEY }), (e) => toastError(e))
  return (
    <div className="nt-share-notes" role="status" data-testid="notes-share-notices">
      <Users size={15} aria-hidden />
      <div className="nt-share-notes-text">
        {notes.slice(-3).map((n) => (
          <div key={n.path}>
            {n.what === 'local_only' ? (
              <>
                {t('notes.sharing.turnedLocalModelsOnlyFor', { by: n.by, value: n.on ? 'on' : 'off' })}{' '}
                <button type="button" className="link" onClick={() => appNavigate(n.kind === 'table' ? tableRoutePath(n.id) : notePath(n.id))}>“{n.name}”</button>.
              </>
            ) : (
              <>
                {t('notes.sharing.shared2', { by: n.by })}{' '}<button type="button" className="link" onClick={() => appNavigate(n.kind === 'table' ? tableRoutePath(n.id) : notePath(n.id))}>“{n.name}”</button>{' '}{t('notes.sharing.withYou')}
              </>
            )}
          </div>
        ))}
        {notes.length > 3 && <div className="muted">{t('notes.sharing.andMore', { value: notes.length - 3 })}</div>}
      </div>
      <button type="button" className="icon-btn xs" aria-label={t('common.dismiss')} data-tip={t('common.dismiss')} onClick={() => void dismiss()}>
        <X size={14} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------- on a card

type Shareable = { level: AccessLevel; owner?: { kind: 'user' | 'group'; name?: string }; sharing?: SharingSummary; changed?: boolean }

/** Does the card show people (initials) or the dot? */
export const hasPeople = (x: Shareable) => !!x.changed || initials(x).length > 0

/** The dot ("changed since you looked") and the initials of the other people on a card. */
export function CardPeople({ item }: { item: Shareable }) {
  const ini = initials(item)
  const line = sharedLine(item)
  return (
    <>
      {item.changed && <span className="nt-dot" role="img" aria-label={t('notes.sharing.changedSinceYouLooked')} data-tip={t('notes.sharing.changedSinceYouLooked')} data-testid="note-changed" />}
      {ini.length > 0 && (
        <span className="nt-card-people" role="img" aria-label={line || t('notes.sharing.shared')} data-tip={line || t('notes.sharing.shared')} data-testid="note-people">
          {ini.map((x, i) => <span key={i} className="nt-initial" aria-hidden>{x}</span>)}
        </span>
      )}
    </>
  )
}

// "Share…" for a calendar (calendar/sharing.py): the people it is shared with and their levels,
// and a picker of the people this server lets the user add. Opened from Calendar's calendar menu
// and from Settings → Calendar & tasks.
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { UserPlus, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Skeleton } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { toast, toastError } from '../../stores/ui'
import { calApi } from './data'
import { SHARE_LEVELS, type Collection, type ShareLevel, type ShareState } from './types'
import { t } from '../../i18n'

const levelName = (l: ShareLevel) => SHARE_LEVELS.find((x) => x.id === l)?.label ?? l

/** The policy line under the title. */
export function policyNote(s: Pick<ShareState, 'policy' | 'own' | 'owner'>): string {
  const base = s.own ? '' : t('calendar.shareDialog.ownsThisCalendarAndLets', { ownerName: s.owner.name })
  if (s.policy === 'nobody') return t('calendar.shareDialog.calendarSharingIsTurnedOff', { base })
  if (s.policy === 'groups') return t('calendar.shareDialog.youCanShareWithPeople', { base })
  return t('calendar.shareDialog.youCanShareWithAnyone', { base })
}

/** People matching the search, by name or address. */
export function matching<T extends { name: string; email: string }>(people: T[], q: string, n = 8): T[] {
  const s = q.trim().toLowerCase()
  const hit = s ? people.filter((p) => p.name.toLowerCase().includes(s) || p.email.toLowerCase().includes(s)) : people
  return hit.slice(0, n)
}

export function ShareDialog({ c, onClose }: { c: Collection; onClose: () => void }) {
  const qc = useQueryClient()
  const key = ['calendar', 'shares', c.calendar_id]
  const q = useQuery<ShareState>({ queryKey: key, queryFn: () => calApi.shares(c), staleTime: 0, retry: false })
  const [search, setSearch] = useState('')
  const [level, setLevel] = useState<ShareLevel>('view')
  const [busy, setBusy] = useState<number | null>(null)
  const s = q.data
  const shown = useMemo(() => matching(s?.candidates ?? [], search), [s?.candidates, search])
  const run = (uid: number, p: Promise<ShareState>, ok: string) => {
    setBusy(uid)
    p.then((next) => {
      qc.setQueryData(key, next)
      void qc.invalidateQueries({ queryKey: ['calendar', 'calendars'] })
      toast({ message: ok })
    }, (e) => toastError(e, t('calendar.shareDialog.couldNotChangeWhoSees'))).finally(() => setBusy(null))
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('calendar.shareDialog.share', { itemName: c.name })}
      footer={<button type="button" className="btn filled" onClick={onClose}>{t('common.done')}</button>}
    >
      <div className="share-dlg" data-testid="share-dialog">
        {q.isLoading ? (
          <Skeleton height={80} />
        ) : q.isError || !s ? (
          <p className="muted" role="alert">{(q.error as Error | null)?.message ?? t('calendar.shareDialog.thisCalendarCannotBeShared')}</p>
        ) : (
          <>
            <p className="muted share-note">{policyNote(s)}</p>
            {s.policy !== 'nobody' && (
              <div className="share-add">
                <div className="row share-add-row">
                  <input
                    className="input"
                    type="search"
                    placeholder={t('calendar.shareDialog.addPeopleByNameOr')}
                    aria-label={t('calendar.shareDialog.findPeople')}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    data-testid="share-search"
                  />
                  <select className="input share-level" aria-label={t('calendar.shareDialog.levelForNewPeople')} value={level} onChange={(e) => setLevel(e.target.value as ShareLevel)} data-testid="share-new-level">
                    {SHARE_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                  </select>
                </div>
                <p className="muted share-level-desc">{SHARE_LEVELS.find((l) => l.id === level)?.desc}</p>
                {s.candidates.length === 0 ? (
                  <p className="muted">{s.people.length ? t('calendar.shareDialog.everyoneYouCanShareWith') : t('calendar.shareDialog.thereIsNobodyYouCan')}</p>
                ) : (
                  <ul className="share-cands" aria-label={t('calendar.shareDialog.peopleYouCanAdd')}>
                    {shown.map((p) => (
                      <li key={p.id} className="share-person" data-testid="share-candidate">
                        <span className="share-who"><b>{p.name}</b><span className="muted">{p.email}</span></span>
                        <button
                          type="button"
                          className="btn tonal sm"
                          disabled={busy !== null}
                          aria-label={t('calendar.shareDialog.shareWith', { itemName: p.name })}
                          // ACT calendar.share @web.calendar @web.settings
                          onClick={() => run(p.id, calApi.setShare(c, p.id, level), `Shared with ${p.name} (${levelName(level).toLowerCase()})`)}
                        >
                          <UserPlus size={14} aria-hidden />{' '}{t('common.add')}
                        </button>
                      </li>
                    ))}
                    {shown.length === 0 && <li className="muted">{t('calendar.shareDialog.nobodyMatches', { search })}</li>}
                  </ul>
                )}
              </div>
            )}
            <h3 className="share-head">{t('calendar.shareDialog.sharedWith')}</h3>
            {s.people.length === 0 ? (
              <p className="muted" data-testid="share-none">{t('calendar.shareDialog.nobodyYetOnlyYouSee')}</p>
            ) : (
              <ul className="share-people" aria-label={t('calendar.shareDialog.peopleWithAccess')}>
                {s.people.map((p) => (
                  <li key={p.id} className="share-person" data-testid="share-person">
                    <span className="share-who">
                      <b>{p.name}{p.you ? t('common.youSuffix') : ''}</b>
                      <span className="muted">
                        {p.email}
                        {p.disabled ? t('calendar.shareDialog.accountDisabled') : ''}
                        {p.outside_policy && !p.disabled ? t('calendar.shareDialog.outsideTheSharingPolicy') : ''}
                      </span>
                    </span>
                    <select
                      className="input share-level"
                      aria-label={t('calendar.shareDialog.levelFor', { itemName: p.name })}
                      value={p.level}
                      disabled={p.you || busy !== null || s.policy === 'nobody'}
                      onChange={(e) => {
                        const l = e.target.value as ShareLevel
                        run(p.id, calApi.setShare(c, p.id, l), `${p.name} can now: ${levelName(l).toLowerCase()}`)
                      }}
                    >
                      {SHARE_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                    </select>
                    {!p.you && (
                      <button
                        type="button"
                        className="icon-btn sm"
                        aria-label={t('calendar.shareDialog.stopSharingWith', { itemName: p.name })}
                        data-tip={t('calendar.shareDialog.stopSharing')}
                        disabled={busy !== null}
                        onClick={() => run(p.id, calApi.unshare(c, p.id), `Stopped sharing with ${p.name}`)}
                      >
                        <X size={16} />
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {s.others > 0 && (
              <p className="muted share-note">
                {t('calendar.shareDialog.alsoSharedWithSetUp', { value: t('calendar.shareDialog.groupsOrAccounts', { count: s.others }) })}
              </p>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}

// Admin → Users → "Invite someone" (backend core/invites.py): a one-time link and a
// QR code for a household member, with setup defaults like "Prepare a new user". The link is shown
// once. The member opens it on a computer or a phone, signs in (or chooses a password, with
// password sign-in) and lands in their own setup guide. The open invites are listed under the
// users while there are any, each with Withdraw.
// ACT household.invite @web.settings, ACT household.invite_revoke @web.settings
// "Email it" opens a draft in mailr's composer (compose.deeplink), never the computer's email app.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Send, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { QrCode } from '../../components/QrCode'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { PrepareFields, presetSummary, type Preset } from './PrepareUser'
import { openComposeWith } from '../compose/mailtoLinks'
import { t, appLocale } from '../../i18n'

export interface Invite {
  id: number
  email: string
  name: string
  defaults: Preset['defaults']
  state: 'ok' | 'used' | 'revoked' | 'expired'
  created_at: string | null
  expires_at: string
  used_at: string | null
  revoked_at: string | null
  created_by: string | null
}

export interface NewInvite extends Invite {
  url: string
  app_url: string
  has_account: boolean
  /** The mail that carries the link, in the new member's language (the server's invites.mail); an older server omits it. */
  mail?: { subject: string; body: string }
}

/** "Tue 13 Oct": the day an invite ends. */
export const endDate = (iso: string) => new Date(iso).toLocaleDateString(appLocale(), { weekday: 'short', day: 'numeric', month: 'short' })

const STATE: Record<Invite['state'], string> = { ok: t('common.waiting'), used: t('settings.inviteMember.joined'), revoked: t('settings.inviteMember.withdrawn'), expired: t('common.expired') }

/** "Waiting · ends in 6 days", "Joined 2 hours ago". */
export function inviteStatus(i: Invite, now = Date.now()): string {
  if (i.state === 'used' && i.used_at) return t('settings.inviteMember.joined2', { relative: formatRelative(i.used_at) })
  if (i.state === 'ok') {
    const days = Math.max(0, Math.ceil((Date.parse(i.expires_at) - now) / 86_400_000))
    return t('settings.inviteMember.waitingEndsInDays', { count: days })
  }
  return STATE[i.state]
}

export function InvitesList() {
  const qc = useQueryClient()
  const q = useQuery<{ items: Invite[] }>({ queryKey: ['admin', 'invites'], queryFn: () => api.get('/admin/invites') })
  const revoke = useMutation({
    mutationFn: (id: number) => api.del(`/admin/invites/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['admin', 'invites'] })
      toast({ message: t('settings.inviteMember.inviteWithdrawnTheLinkNo') })
    },
    onError: (e) => toastError(e),
  })
  // Shown while an invite is waiting, or joined or ended in the last two weeks.
  const recent = (q.data?.items ?? []).filter((i) => i.state === 'ok' || Date.now() - Date.parse(i.used_at ?? i.revoked_at ?? i.expires_at) < 14 * 86_400_000)
  if (!recent.length) return null
  return (
    <div className="invites" data-testid="invites-list">
      <b style={{ display: 'block', margin: '16px 0 4px' }}>{t('settings.inviteMember.invites')}</b>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>{t('settings.inviteMember.for')}</th>
              <th>{t('common.setup')}</th>
              <th>{t('common.status')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {recent.map((i) => (
              <tr key={i.id}>
                <td>
                  <b>{i.name || i.email}</b>
                  {i.name && <div className="muted" style={{ fontSize: 12 }}>{i.email}</div>}
                </td>
                <td style={{ fontSize: 13 }}>{presetSummary({ defaults: i.defaults ?? {} })}</td>
                <td style={{ fontSize: 13 }}>{inviteStatus(i)}</td>
                <td>
                  {i.state === 'ok' && (
                    <button type="button" className="icon-btn" aria-label={t('settings.inviteMember.withdrawTheInviteFor', { email: i.email })} data-tip={t('settings.inviteMember.withdraw')} onClick={() => revoke.mutate(i.id)}>
                      <Trash2 />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function InviteDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [days, setDays] = useState(7)
  const [d, setD] = useState<Preset['defaults']>({ assistant: true })
  const [made, setMade] = useState<NewInvite | null>(null)
  const set = (k: keyof Preset['defaults'], v: string | boolean) => setD((x) => ({ ...x, [k]: v === '' ? undefined : v }))
  const create = useMutation({
    mutationFn: () => api.post<NewInvite>('/admin/invites', { email: email.trim(), name: name.trim(), days, defaults: d }),
    onSuccess: (r) => {
      setMade(r)
      void qc.invalidateQueries({ queryKey: ['admin', 'invites'] })
    },
    onError: (e) => toastError(e, t('settings.inviteMember.inviteNotMade')),
  })
  const copy = (text: string) =>
    void navigator.clipboard?.writeText(text).then(() => toast({ message: t('common.linkCopied') }), () => toast({ message: text }))
  if (made) {
    const who = made.name || made.email
    return (
      <Dialog open onClose={onClose} title={t('settings.inviteMember.inviteFor', { who })} modal footer={<button type="button" className="btn filled" onClick={onClose}>{t('common.done')}</button>}>
        <div className="invite-made" data-testid="invite-made">
          <QrCode text={made.url} label={t('settings.inviteMember.qrCodeOfTheInvite', { who })} />
          <div className="stack" style={{ gap: 8, minWidth: 0 }}>
            <p style={{ margin: 0 }}>
              {t('settings.inviteMember.opensThisLinkOnA', { who, endDate: endDate(made.expires_at) })}
            </p>
            <div className="row" style={{ gap: 8 }}>
              <input className="input" readOnly value={made.url} aria-label={t('settings.inviteMember.inviteLink')} onFocus={(e) => e.currentTarget.select()} />
              <button type="button" className="btn tonal" onClick={() => copy(made.url)}>
                <Copy />{' '}{t('common.copy')}
              </button>
            </div>
            {/* Email it: a draft in mailr's own composer, to the invited address, with the link and its
                last day (the server's invites.mail, in the new member's language). Nothing is sent
                until you press Send. ACT compose.deeplink @web.settings */}
            <button type="button" className="btn text sm" style={{ alignSelf: 'flex-start' }} data-testid="invite-email-it" onClick={() => {
              const mail = made.mail ?? {
                subject: t('settings.inviteMember.mailSubject'),
                body: t('settings.inviteMember.mailBody', { url: made.url, endDate: endDate(made.expires_at) }),
              }
              openComposeWith({ to: made.email ? [made.name ? { email: made.email, name: made.name } : { email: made.email }] : [], cc: [], bcc: [], subject: mail.subject, body: mail.body })
              toast({ message: t('settings.inviteMember.draftHasTheLink') })
              onClose()
            }}>
              <Send />{' '}{t('settings.inviteMember.emailIt')}
            </button>
            <p className="muted" style={{ fontSize: 13, margin: 0 }}>
              {t('settings.inviteMember.theLinkIsShownOnly', { value: made.has_account
                ? t('settings.inviteMember.theyAlreadyHaveAnAccount')
                : t('settings.inviteMember.theySignInWithThe') })}
            </p>
          </div>
        </div>
      </Dialog>
    )
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.inviteMember.inviteSomeone')}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn filled" disabled={!email.includes('@') || create.isPending} onClick={() => create.mutate()}>
            {t('settings.inviteMember.createLink')}
          </button>
        </>
      }
    >
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>{t('settings.inviteMember.intro')}</p>
        <div className="grid-2">
          <div className="field">
            <label htmlFor="inv-name">{t('common.name')}</label>
            <input id="inv-name" className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </div>
          <div className="field">
            <label htmlFor="inv-email">{t('settings.inviteMember.email')}</label>
            <input id="inv-email" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
              aria-describedby="inv-email-hint" />
            <span className="hint" id="inv-email-hint">{t('settings.inviteMember.emailHint')}</span>
          </div>
        </div>
        <div className="field">
          <label htmlFor="inv-days">{t('settings.inviteMember.expiresAfter')}</label>
          <select id="inv-days" className="select" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 3, 7, 14, 30].map((n) => <option key={n} value={n}>{t('settings.inviteMember.days', { count: n })}</option>)}
          </select>
        </div>
        <details className="invite-prepare">
          <summary>{t('settings.inviteMember.setupDefaults')}</summary>
          <div className="stack" style={{ marginTop: 8 }}>
            <PrepareFields d={d} set={set} />
          </div>
        </details>
      </div>
    </Dialog>
  )
}

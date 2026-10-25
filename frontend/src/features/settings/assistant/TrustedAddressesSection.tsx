// Settings → Ambrose → Trusted addresses (backend/mailr/core/trusted.py, decisions
// 2026-10-09-trusted-forwards and 2026-10-09-orders-consolidate). The identity page only: addresses of
// yours that mailr is not connected to (a work account), each confirmed with the code mailr mails to
// it, and the addresses another assistant handles. What Ambrose does with a note you forward is the
// built-in standing order "Follow my forwarded notes" (Standing orders).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleCheck, Mail, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { api } from '../../../api/client'
import { Skeleton } from '../../../components/bits'
import { ConfirmDialog } from '../../../components/Dialog'
import { formatRelative } from '../../../lib/format'
import { toast, toastError } from '../../../stores/ui'
import { useAssistantName } from '../../rail/assistant/useAssistantName'
import { useSettings, useUpdateSettings } from '../../../api/hooks'
import { Section } from '../common'
import { t } from '../../../i18n'

export interface TrustedAddress {
  id: number
  email: string
  state: 'confirmed' | 'pending' | 'expired'
  confirmed_at: string | null
  code_expires_at: string | null
  last_message: { id: string; subject: string; at: string | null; verified: boolean } | null
}

export interface TrustedListing {
  addresses: TrustedAddress[]
  suggestions: { email: string; name: string; messages: number; last_at: string | null }[]
  /** The addresses another assistant handles (the setting handled_elsewhere). */
  handled_elsewhere?: string[]
  code_minutes: number
}

export const TRUSTED_KEY = ['assistant', 'trusted-addresses'] as const
const BASE = '/assistant/trusted-addresses'

function useTrusted() {
  return useQuery<TrustedListing>({ queryKey: TRUSTED_KEY, queryFn: ({ signal }) => api.get(BASE, undefined, signal) })
}

/** The state of an address, in words: confirmed, a code is out, or the code ran out. */
export function stateText(a: Pick<TrustedAddress, 'state'>): string {
  return a.state === 'confirmed' ? t('settings.assistant.trustedSection.confirmed')
    : a.state === 'pending' ? t('settings.assistant.trustedSection.codeSent') : t('settings.assistant.trustedSection.codeExpired')
}

function AddressRow({ a, minutes }: { a: TrustedAddress; minutes: number }) {
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [removing, setRemoving] = useState(false)
  const refresh = () => void qc.invalidateQueries({ queryKey: TRUSTED_KEY })
  // ACT trusted.confirm @web.settings
  const confirm = useMutation({
    mutationFn: () => api.post<TrustedAddress>(`${BASE}/${a.id}/confirm`, { code: code.trim() }),
    onSuccess: () => { setCode(''); toast({ message: t('settings.assistant.trustedSection.addressConfirmed', { email: a.email }) }); refresh() },
    onError: (e) => { toastError(e, t('settings.assistant.trustedSection.notConfirmed')); refresh() },
  })
  // ACT trusted.resend @web.settings
  const resend = useMutation({
    mutationFn: () => api.post<TrustedAddress>(`${BASE}/${a.id}/resend`, {}),
    onSuccess: () => { toast({ message: t('settings.assistant.trustedSection.codeSentTo', { email: a.email, count: minutes }) }); refresh() },
    onError: (e) => toastError(e, t('settings.assistant.trustedSection.codeNotSent')),
  })
  // ACT trusted.remove @web.settings
  const remove = useMutation({
    mutationFn: () => api.del(`${BASE}/${a.id}`),
    onSuccess: () => { setRemoving(false); toast({ message: t('settings.assistant.trustedSection.removed', { email: a.email }) }); refresh() },
    onError: (e) => toastError(e, t('common.notChanged')),
  })
  const last = a.last_message
  return (
    <li className="tr-row" data-testid="trusted-address" data-state={a.state}>
      <div className="tr-main">
        <Mail size={16} aria-hidden />
        <span className="tr-email">{a.email}</span>
        <span className={`pill ${a.state === 'confirmed' ? 'ok' : 'info'}`}>{stateText(a)}</span>
        <button type="button" className="btn text sm tr-remove" onClick={() => setRemoving(true)}>{t('common.remove')}</button>
      </div>
      {a.state === 'confirmed' && last && (
        <p className="tr-last muted" data-testid="trusted-last">
          {last.verified ? <CircleCheck size={14} aria-hidden /> : <TriangleAlert size={14} aria-hidden />}{' '}
          {last.verified
            ? t('settings.assistant.trustedSection.lastMailVerified', { when: last.at ? formatRelative(last.at) : '' })
            : t('settings.assistant.trustedSection.lastMailNotVerified', { when: last.at ? formatRelative(last.at) : '' })}
        </p>
      )}
      {a.state !== 'confirmed' && (
        <form className="tr-code" onSubmit={(e) => { e.preventDefault(); if (code.trim()) confirm.mutate() }}>
          <label>
            <span className="sr-only">{t('settings.assistant.trustedSection.codeFor', { email: a.email })}</span>
            <input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={12} value={code}
              placeholder={t('settings.assistant.trustedSection.code')} onChange={(e) => setCode(e.target.value)}
              disabled={a.state === 'expired'} aria-label={t('settings.assistant.trustedSection.codeFor', { email: a.email })} />
          </label>
          <button type="submit" className="btn tonal sm" disabled={!code.trim() || confirm.isPending || a.state === 'expired'}>
            {t('settings.assistant.trustedSection.confirm')}
          </button>
          <button type="button" className="btn text sm" disabled={resend.isPending} onClick={() => resend.mutate()}>
            {t('settings.assistant.trustedSection.sendANewCode')}
          </button>
        </form>
      )}
      <ConfirmDialog open={removing} title={t('settings.assistant.trustedSection.removeTitle', { email: a.email })}
        body={t('settings.assistant.trustedSection.removeBody')} confirmLabel={t('common.remove')} danger
        busy={remove.isPending} onConfirm={() => remove.mutate()} onClose={() => setRemoving(false)} />
    </li>
  )
}

/** ACT trusted.list @web.settings */
export function TrustedAddressesSection() {
  const name = useAssistantName()
  const qc = useQueryClient()
  const q = useTrusted()
  const [email, setEmail] = useState('')
  // ACT trusted.add @web.settings
  const add = useMutation({
    mutationFn: (addr: string) => api.post<TrustedAddress>(BASE, { email: addr }),
    onSuccess: (a) => {
      setEmail('')
      toast({ message: t('settings.assistant.trustedSection.codeSentTo', { email: a.email, count: q.data?.code_minutes ?? 30 }) })
      void qc.invalidateQueries({ queryKey: TRUSTED_KEY })
    },
    onError: (e) => toastError(e, t('settings.assistant.trustedSection.codeNotSent')),
  })
  const d = q.data
  return (
    <Section title={t('settings.assistant.trustedSection.title')} desc={t('settings.assistant.trustedSection.desc', { name })}>
      {!d ? <Skeleton height={88} /> : (
        <div className="tr-list" data-testid="trusted-addresses">
          {d.addresses.length === 0
            ? <p className="muted">{t('settings.assistant.trustedSection.none')}</p>
            : <ul className="tr-rows">{d.addresses.map((a) => <AddressRow key={a.id} a={a} minutes={d.code_minutes} />)}</ul>}
          <form className="tr-add" onSubmit={(e) => { e.preventDefault(); if (email.trim()) add.mutate(email.trim()) }}>
            <input className="input" type="email" value={email} placeholder={t('settings.assistant.trustedSection.placeholder')}
              aria-label={t('settings.assistant.trustedSection.addressOfYours')} onChange={(e) => setEmail(e.target.value)} />
            <button type="submit" className="btn tonal sm" disabled={!email.trim() || add.isPending}>{t('settings.assistant.trustedSection.sendCode')}</button>
          </form>
          <p className="as-hint">{t('settings.assistant.trustedSection.howItWorks', { count: d.code_minutes })}</p>
          <p className="as-hint tr-order" data-testid="trusted-order-link">
            {t('settings.assistant.trustedSection.followOrder', { name })}{' '}
            <Link to="/settings/ambrose/orders">{t('settings.groups.standingOrdersRoutines')}</Link>
          </p>
          {d.suggestions.length > 0 && (
            <div className="tr-suggest" data-testid="trusted-suggestions">
              <h3>{t('settings.assistant.trustedSection.theseLookLikeYours')}</h3>
              <ul>
                {d.suggestions.map((s) => (
                  <li key={s.email}>
                    <span className="tr-email">{s.email}</span>
                    <span className="muted">{t('settings.assistant.trustedSection.messagesFrom', { count: s.messages })}</span>
                    <button type="button" className="btn text sm" disabled={add.isPending} onClick={() => add.mutate(s.email)}>
                      {t('settings.assistant.trustedSection.sendCode')}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

/** Settings → Ambrose → Trusted addresses → Addresses for other assistants (the user
 * setting handled_elsewhere): standing orders leave mail sent only to these alone, unless an order
 * names the address. Another program (say one that reads memory@) acts on that mail instead. */
export function HandledElsewhereSection() {
  const settings = useSettings()
  const update = useUpdateSettings()
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const list = settings.handled_elsewhere ?? []
  const save = (next: string[], done?: string) => update.mutate({ handled_elsewhere: next }, {
    onSuccess: () => { if (done) toast({ message: done }); void qc.invalidateQueries({ queryKey: TRUSTED_KEY }) },
  })
  const add = () => {
    const a = email.trim().toLowerCase()
    if (!a) return
    setEmail('')
    if (!list.includes(a)) save([...list, a], t('settings.assistant.trustedSection.elsewhereAdded', { email: a }))
  }
  return (
    <Section title={t('settings.assistant.trustedSection.elsewhereTitle')} desc={t('settings.assistant.trustedSection.elsewhereDesc')}>
      <div className="tr-list" data-testid="handled-elsewhere">
        {list.length === 0
          ? <p className="muted">{t('settings.assistant.trustedSection.elsewhereNone')}</p>
          : <ul className="tr-rows">{list.map((a) => (
            <li key={a} className="tr-row">
              <div className="tr-main">
                <Mail size={16} aria-hidden />
                <span className="tr-email">{a}</span>
                <button type="button" className="btn text sm tr-remove" disabled={update.isPending}
                  onClick={() => save(list.filter((x) => x !== a), t('settings.assistant.trustedSection.elsewhereRemoved', { email: a }))}>
                  {t('common.remove')}
                </button>
              </div>
            </li>
          ))}</ul>}
        <form className="tr-add" onSubmit={(e) => { e.preventDefault(); add() }}>
          <input className="input" type="email" value={email} placeholder={t('settings.assistant.trustedSection.elsewherePlaceholder')}
            aria-label={t('settings.assistant.trustedSection.elsewhereAddress')} onChange={(e) => setEmail(e.target.value)} />
          <button type="submit" className="btn tonal sm" disabled={!email.trim() || update.isPending}>{t('common.add')}</button>
        </form>
      </div>
    </Section>
  )
}

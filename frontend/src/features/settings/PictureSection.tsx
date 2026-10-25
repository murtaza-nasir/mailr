import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Settings → You → Your picture: the account picture (one per user) and the helpers for
// showing a picture outside mailr. The guidance depends on the user's sending addresses: Google and
// Microsoft account photos, Gravatar/Libravatar for the rest, and BIMI checks for admins only.
// Backend: api/account_picture_routes.py.

import { Check as CheckIcon, ExternalLink, Minus, X } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk, useApi, useMe } from '../../api/hooks'
import type { AccountPicture } from '../../api/types'
import { Avatar } from '../../components/bits'
import { resetMissing } from '../../lib/avatars'
import { toast, toastError } from '../../stores/ui'
import { Section } from './common'
import { t, tx } from '../../i18n'

type Found = 'found' | 'not_found' | 'unknown'
interface PublishItem { identity_id: number; email: string; gravatar: Found; libravatar: Found; provider: 'google' | 'microsoft' | null; shared_domain: boolean }
export interface ProviderItem { identity_id: number; email: string; provider: 'google' | 'microsoft' | null; domain: string; shared_domain: boolean }
export interface BimiRequirement { key: string; label: string; met: boolean | null; detail: string; next: string }
export interface BimiResult { domain: string; applicable: boolean; reason: string; requirements: BimiRequirement[]; ready?: boolean }

export function pictureSourceText(p: AccountPicture | undefined): string {
  if (!p?.has) return t('settings.pictureSection.noPictureYetYourInitials')
  return p.source === 'idp' ? t('settings.pictureSection.fromYourSignInProvider') : t('settings.pictureSection.uploadedByYou')
}

/** Domains to offer a BIMI check for: one per domain, and never a shared mailbox provider's domain
 * (`shared_domain` comes from picture_checks.SHARED_DOMAINS on the server). */
export function bimiDomains(items: ProviderItem[]): { domain: string; identity_id: number }[] {
  const seen = new Map<string, { domain: string; identity_id: number }>()
  for (const i of items) if (!i.shared_domain && !seen.has(i.domain)) seen.set(i.domain, { domain: i.domain, identity_id: i.identity_id })
  return [...seen.values()].sort((a, b) => a.domain.localeCompare(b.domain))
}

export interface PublishGuidance {
  google: ProviderItem[]
  microsoft: ProviderItem[]
  /** Addresses for Gravatar and Libravatar: those that send through neither Google nor Microsoft. */
  other: ProviderItem[]
  /** BIMI checks: admins only, own domains only. */
  bimi: { domain: string; identity_id: number }[]
}

/** Which parts of "Publish your picture" apply to these sending addresses. */
export function publishGuidance(items: ProviderItem[], admin: boolean): PublishGuidance {
  return {
    google: items.filter((i) => i.provider === 'google'),
    microsoft: items.filter((i) => i.provider === 'microsoft'),
    other: items.filter((i) => i.provider === null),
    bimi: admin ? bimiDomains(items) : [],
  }
}

export function PictureSection() {
  const me = useMe().data
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const pic = me?.picture
  const refresh = () => {
    resetMissing()
    void qc.invalidateQueries({ queryKey: qk.me })
    void qc.invalidateQueries({ queryKey: ['pictures', 'local'] })
  }
  const run = async (fn: () => Promise<unknown>, ok: string, fail: string) => {
    setBusy(true)
    try {
      await fn()
      refresh()
      toast({ message: ok })
    } catch (e) {
      toastError(e, fail)
    } finally {
      setBusy(false)
    }
  }
  // ACT settings.picture_upload @web.settings
  const upload = (f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    void run(() => api.put('/me/picture', fd), 'Picture updated.', 'Picture not saved')
  }
  // ACT settings.picture_remove @web.settings
  const remove = () => void run(() => api.del('/me/picture'), 'Picture removed.', 'Could not remove the picture')
  // ACT settings.picture_use_sign_in @web.settings
  const useSignIn = () => void run(() => api.post('/me/picture/use-sign-in'), 'Using your sign-in picture.', 'Could not use the sign-in picture')

  return (
    <Section
      id="picture"
      title={t('common.yourPicture')}
      desc={t('settings.pictureSection.shownNextToYourName', { name: assistantNameNow() })}
    >
      <div className="row picture-row" style={{ gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="picture-large" data-testid="account-picture">
          <Avatar key={pic?.version ?? 0} who={me ? { email: me.user.email, name: me.user.name } : null} src={pic?.has ? `/api/v1/me/picture?v=${pic.version}` : null} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="muted" data-testid="picture-source">{pictureSourceText(pic)}</div>
          <div className="row" style={{ gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn tonal" disabled={busy} onClick={() => input.current?.click()}>
              {pic?.has ? t('settings.pictureSection.replacePicture') : t('settings.pictureSection.uploadPicture')}
            </button>
            {pic?.idp_available && pic.source !== 'idp' && (
              <button type="button" className="btn text" disabled={busy} onClick={useSignIn}>
                {t('settings.pictureSection.useMySignInPicture')}
              </button>
            )}
            {pic?.has && (
              <button type="button" className="btn text" disabled={busy} onClick={remove}>
                {t('common.remove')}
              </button>
            )}
          </div>
          <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
            {t('settings.pictureSection.pngJpegGifOrWebp')}
          </div>
        </div>
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          hidden
          aria-label={t('settings.pictureSection.chooseAPicture')}
          onChange={(e) => {
            const f = e.target.files?.[0]
            e.target.value = ''
            if (f) upload(f)
          }}
        />
      </div>
      <PublishHelp />
    </Section>
  )
}

function Mark({ v }: { v: boolean | null | Found }) {
  if (v === true || v === 'found') return <span className="pill ok"><CheckIcon size={12} aria-hidden /> {v === 'found' ? 'found' : 'met'}</span>
  if (v === false || v === 'not_found') return <span className="pill warn"><X size={12} aria-hidden /> {v === 'not_found' ? t('settings.pictureSection.notFound') : t('settings.pictureSection.notMet')}</span>
  return <span className="pill"><Minus size={12} aria-hidden /> {v === 'unknown' ? t('settings.pictureSection.noAnswer') : t('settings.pictureSection.notChecked')}</span>
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children} <ExternalLink size={12} aria-hidden />
    </a>
  )
}

function PublishHelp() {
  const admin = useMe().data?.user.role === 'admin'
  const providers = useApi<{ items: ProviderItem[] }>(['pictures', 'providers'], '/me/picture/providers')
  const [check, setCheck] = useState<PublishItem[] | null>(null)
  const [checking, setChecking] = useState(false)
  const [bimi, setBimi] = useState<Record<string, BimiResult | 'busy'>>({})
  if (!providers.data) return null
  const items = providers.data.items
  const g = publishGuidance(items, admin)
  // No sending address yet: the general steps still help.
  const showGravatar = g.other.length > 0 || items.length === 0

  // ACT settings.picture_publish_check @web.settings
  const runCheck = async () => {
    setChecking(true)
    try {
      const ids = g.other.map((i) => i.identity_id)
      setCheck((await api.post<{ items: PublishItem[] }>('/me/picture/publish-check', { identity_ids: ids })).items)
    } catch (e) {
      toastError(e, t('settings.pictureSection.couldNotCheck'))
    } finally {
      setChecking(false)
    }
  }
  // ACT settings.picture_bimi_check @web.settings
  const runBimi = async (domain: string, identity_id: number) => {
    setBimi((b) => ({ ...b, [domain]: 'busy' }))
    try {
      const r = await api.post<BimiResult>('/me/picture/bimi-check', { identity_id })
      setBimi((b) => ({ ...b, [domain]: r }))
    } catch (e) {
      setBimi((b) => {
        const n = { ...b }
        delete n[domain]
        return n
      })
      toastError(e, t('settings.pictureSection.couldNotCheckBimi'))
    }
  }
  const emails = (l: ProviderItem[]) => l.map((i) => i.email).join(', ')

  return (
    <details className="picture-publish" style={{ marginTop: 16 }}>
      <summary>
        <b>{t('settings.pictureSection.publishYourPicture')}</b> <span className="muted">{t('settings.pictureSection.soOtherMailAppsShow')}</span>
      </summary>
      <p>{t('settings.pictureSection.gmailOutlookAppleMailAnd')}</p>
      {g.google.length > 0 && (
        <>
          <h3 className="picture-h">{t('settings.pictureSection.googleAccountPhoto')}</h3>
          <p>
            {tx('settings.pictureSection.gmailShowsThePhotoOn', { emails: emails(g.google) }, { ext: (c) => <Ext href="https://myaccount.google.com/personal-info">{c}</Ext> })}
          </p>
        </>
      )}
      {g.microsoft.length > 0 && (
        <>
          <h3 className="picture-h">{t('settings.pictureSection.microsoftAccountPhoto')}</h3>
          <p>
            {tx('settings.pictureSection.outlookShowsThePhotoOn', { emails: emails(g.microsoft) }, { ext: (c) => <Ext href="https://account.microsoft.com/profile">{c}</Ext>, ext2: (c) => <Ext href="https://myaccount.microsoft.com">{c}</Ext> })}
          </p>
        </>
      )}
      {showGravatar && (
        <>
          <h3 className="picture-h">{t('settings.pictureSection.gravatarAndLibravatar')}</h3>
          <p>
            {tx('settings.pictureSection.someMailAppsShowThe', { value: g.other.length > 0 ? emails(g.other) : t('settings.pictureSection.theAddressYouSendFrom') }, { ext: (c) => <Ext href="https://gravatar.com/signup">{c}</Ext>, ext2: (c) => <Ext href="https://www.libravatar.org/accounts/new/">{c}</Ext> })}
          </p>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn tonal" disabled={checking || g.other.length === 0} onClick={() => void runCheck()}>
              {checking ? t('common.checking') : t('settings.pictureSection.checkMyAddresses')}
            </button>
            <span className="muted" style={{ fontSize: 12.5 }}>{t('settings.pictureSection.asksGravatarAndLibravatarAbout')}</span>
          </div>
          {check && (
            <div className="table-wrap" style={{ marginTop: 8 }}>
              <table className="table" data-testid="publish-check">
                <thead>
                  <tr>
                    <th>{t('common.address')}</th>
                    <th>{t('settings.pictureSection.gravatar')}</th>
                    <th>{t('settings.pictureSection.libravatar')}</th>
                  </tr>
                </thead>
                <tbody>
                  {check.map((c) => (
                    <tr key={c.identity_id}>
                      <td className="ellipsis">{c.email}</td>
                      <td><Mark v={c.gravatar} /></td>
                      <td><Mark v={c.libravatar} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {g.bimi.length > 0 && (
        <>
          <h3 className="picture-h">{t('settings.pictureSection.bimiBrandLogo')}</h3>
          <p>
            {t('settings.pictureSection.forADomainYouOwn')}
          </p>
          {g.bimi.map((d) => {
            const r = bimi[d.domain]
            return (
              <div key={d.domain} className="card picture-bimi" style={{ marginBottom: 8 }}>
                <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                  <b style={{ flex: 1 }}>{d.domain}</b>
                  <button type="button" className="btn text sm" disabled={r === 'busy'} onClick={() => void runBimi(d.domain, d.identity_id)}>
                    {r === 'busy' ? t('common.checking') : r ? t('settings.pictureSection.checkAgain') : t('settings.pictureSection.checkBimi')}
                  </button>
                </div>
                {r && r !== 'busy' && <BimiReport result={r} />}
              </div>
            )
          })}
        </>
      )}
    </details>
  )
}

export function BimiReport({ result }: { result: BimiResult }) {
  if (!result.applicable) return <p className="muted">{result.reason}</p>
  return (
    <ul className="picture-reqs" data-testid="bimi-report">
      {result.requirements.map((q) => (
        <li key={q.key}>
          <Mark v={q.met} /> <b>{q.label}</b>
          <div className="muted" style={{ fontSize: 12.5 }}>{q.detail}</div>
          {q.next && <div style={{ fontSize: 13 }}>{q.next}</div>}
        </li>
      ))}
      {result.ready && <li>{tx('settings.pictureSection.everythingIsInPlaceMail', undefined, { b: (c) => <b>{c}</b> })}</li>}
    </ul>
  )
}

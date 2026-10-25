// /join/:token: the page a household invite opens (backend api/invite_routes.py).
// It needs no sign-in. Signed out, it greets the person by name and offers the instance's sign-in:
// single sign-on (which comes back here) or, with password sign-in, a password of their own. Signed
// in as the invited address, it takes the invite and opens the setup guide. On an Android phone it
// also offers the mailr app, with the server and the invite filled in.
// ACT household.join @web.setup

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, LogIn, Smartphone } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, buildUrl, setCsrf } from '../../api/client'
import { qk } from '../../api/hooks'
import { Logo, Spinner } from '../../components/bits'
import { Toasts } from '../../components/Toasts'
import { useApplyTheme } from '../../hooks/useTheme'
import { detectPhone } from '../onboarding/steps'
import './login.css'
import { t } from '../../i18n'

export interface InviteInfo {
  state: 'ok' | 'used' | 'revoked' | 'expired'
  email: string
  name: string
  server: string
  has_account: boolean
  set_password: boolean
  sign_in: { oidc: boolean; oidc_name: string; local_login: boolean }
  signed_in: { email: string; matches: boolean } | null
  expires_at: string
  phone: { app_url: string; apk_url: string; get_app: string }
}

const ENDED: Record<string, string> = {
  used: t('auth.joinPage.thisInviteWasUsedAlready'),
  revoked: t('auth.joinPage.thisInviteWasWithdrawnAsk'),
  expired: t('auth.joinPage.thisInviteHasExpiredAsk'),
}

/** What the page offers a signed-out visitor: single sign-on, a new password, or the sign-in page. */
export function joinChoices(i: Pick<InviteInfo, 'sign_in' | 'set_password' | 'has_account'>): { sso: boolean; password: boolean; signIn: boolean } {
  const sso = i.sign_in.oidc
  const password = i.set_password
  return { sso, password, signIn: !sso && !password && i.has_account }
}

export function JoinPage() {
  useApplyTheme()
  const { token = '' } = useParams()
  const nav = useNavigate()
  const qc = useQueryClient()
  const q = useQuery<InviteInfo>({
    queryKey: ['invite', token],
    queryFn: ({ signal }) => api.get<InviteInfo>(`/invites/${encodeURIComponent(token)}`, undefined, signal),
    retry: false,
  })
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const accepting = useRef(false)
  const inv = q.data

  const finish = async (next: string) => {
    await qc.invalidateQueries({ queryKey: qk.me })
    nav(next.startsWith('/') ? next : '/mail/inbox', { replace: true })
  }
  // Signed in as the invited address (back from single sign-on, or already signed in): take it.
  useEffect(() => {
    if (!inv || inv.state !== 'ok' || !inv.signed_in?.matches || accepting.current) return
    accepting.current = true
    void (async () => {
      try {
        const s = await api.get<{ csrf?: string }>('/auth/session')
        if (s.csrf) setCsrf(s.csrf)
        const r = await api.post<{ next: string }>(`/invites/${encodeURIComponent(token)}/accept`, {})
        await finish(r.next)
      } catch (e) {
        setError((e as Error).message)
        accepting.current = false
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inv, token])

  const choose = async () => {
    if (pw !== pw2) {
      setError(t('auth.joinPage.theTwoPasswordsDiffer'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      const r = await api.post<{ next: string; csrf: string }>(`/invites/${encodeURIComponent(token)}/accept`, { password: pw }, { quiet401: true })
      setCsrf(r.csrf)
      await finish(r.next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const phone = detectPhone(navigator.userAgent, navigator.maxTouchPoints ?? 0)
  const host = window.location.host
  const who = inv?.name?.split(' ')[0]
  const c = inv ? joinChoices(inv) : null
  return (
    <div className="login-page" data-testid="join-page">
      <main className="login-card">
        <div className="login-brand">
          <Logo size={56} />
          <span className="login-word"><b>Mail</b>r</span>
        </div>
        {q.isLoading ? (
          <div className="login-loading"><Spinner /></div>
        ) : q.isError || !inv ? (
          <>
            <h1 className="login-title">{t('auth.joinPage.thisLinkDoesNotWork')}</h1>
            <p className="login-sub">{t('auth.joinPage.theInviteLinkIsNot')}</p>
            <Link className="btn tonal" to="/login">{t('common.signIn')}</Link>
          </>
        ) : inv.state !== 'ok' ? (
          <>
            <h1 className="login-title">{inv.state === 'used' ? t('auth.joinPage.youHaveJoinedAlready') : t('auth.joinPage.thisInviteHasEnded')}</h1>
            <p className="login-sub">{ENDED[inv.state]}</p>
            <Link className="btn tonal" to="/login">{t('common.signIn')}</Link>
          </>
        ) : inv.signed_in && !inv.signed_in.matches ? (
          <>
            <h1 className="login-title">{t('auth.joinPage.thisInviteIsFor', { email: inv.email })}</h1>
            <p className="login-sub">{t('auth.joinPage.youAreSignedInAs', { email: inv.signed_in.email })}</p>
            <button type="button" className="btn tonal" onClick={() => void api.post('/auth/logout').finally(() => window.location.reload())}>
              {t('common.signOut')}
            </button>
          </>
        ) : inv.signed_in?.matches ? (
          <>
            <h1 className="login-title">{t('auth.joinPage.settingThingsUp')}</h1>
            {error ? <div className="banner error" role="alert">{error}</div> : <div className="login-loading"><Spinner /></div>}
          </>
        ) : (
          <>
            <h1 className="login-title">{who ? t('auth.joinPage.welcome', { who }) : t('auth.joinPage.welcome2')}</h1>
            <p className="login-sub">
              {t('auth.joinPage.youAreInvitedToMailr', { host, email: inv.email })}
            </p>
            <div className="stack login-actions">
              {error && <div className="banner error" role="alert">{error}</div>}
              {c?.sso && (
                <a className="btn filled login-sso" href={buildUrl('/auth/oidc/start', { next: `/join/${token}` })}>
                  <KeyRound />{' '}{t('auth.joinPage.continueWith', { oidcName: inv.sign_in.oidc_name || t('auth.loginPage.singleSignOn') })}
                </a>
              )}
              {c?.sso && c.password && (
                <div className="login-or"><span />{t('auth.joinPage.orChooseAPassword')}<span /></div>
              )}
              {c?.password && (
                <form className="stack" onSubmit={(e) => { e.preventDefault(); void choose() }}>
                  <input type="email" autoComplete="username" value={inv.email} readOnly hidden />
                  <div className="field">
                    <label htmlFor="join-pw">{t('auth.joinPage.chooseAPasswordAtLeast')}</label>
                    <input id="join-pw" className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus={!c.sso} />
                  </div>
                  <div className="field">
                    <label htmlFor="join-pw2">{t('auth.joinPage.typeItAgain')}</label>
                    <input id="join-pw2" className="input" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} />
                  </div>
                  <button type="submit" className={`btn ${c.sso ? 'tonal' : 'filled'} login-submit`} disabled={busy || pw.length < 10 || !pw2}>
                    {busy ? <Spinner small /> : <LogIn />}{' '}{t('common.start')}
                  </button>
                </form>
              )}
              {c?.signIn && (
                <Link className="btn filled" to={`/login?next=${encodeURIComponent(`/join/${token}`)}`}>
                  <LogIn />{' '}{t('common.signIn')}
                </Link>
              )}
              {phone === 'android' && (
                <div className="join-phone">
                  <a className="btn text" href={inv.phone.app_url} data-testid="join-open-app">
                    <Smartphone />{' '}{t('auth.joinPage.openInTheMailrApp')}
                  </a>
                  <a className="btn text" href={inv.phone.get_app}>{t('auth.joinPage.getTheApp')}</a>
                </div>
              )}
            </div>
          </>
        )}
        <p className="login-foot">{host}</p>
      </main>
      <Toasts />
    </div>
  )
}

import { CalendarDays, CheckSquare, KeyRound, LogIn, Mail } from 'lucide-react'
import './login.css'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, buildUrl, setCsrf } from '../../api/client'
import { qk, useAuthConfig } from '../../api/hooks'
import { Logo, Spinner } from '../../components/bits'
import { useApplyTheme } from '../../hooks/useTheme'
import { t } from '../../i18n'

/** Pages the server answers, not the web app: the help pages (/help/) load as a whole page. */
function isServerPage(next: string): boolean {
  return /^\/help(\/|$|\?)/.test(next)
}

export function LoginPage() {
  useApplyTheme()
  const cfg = useAuthConfig()
  const [params] = useSearchParams()
  const next = params.get('next') || '/mail/inbox'
  const oidcError = params.get('error')
  const nav = useNavigate()
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(oidcError)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.post<{ csrf: string }>('/auth/login', { email: email.trim(), password }, { quiet401: true })
      setCsrf(res.csrf)
      await qc.invalidateQueries({ queryKey: qk.me })
      if (isServerPage(next)) window.location.replace(next)
      else nav(next.startsWith('/') && !next.startsWith('//') ? next : '/mail/inbox', { replace: true })
    } catch (e) {
      setError((e as { status?: number }).status === 401 ? t('auth.loginPage.wrongEmailOrPassword') : (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const sso = cfg.data?.oidc
  return (
    <div className="login-page">
      <main className="login-card">
        <div className="login-brand">
          <Logo size={56} />
          <span className="login-word"><b>Mail</b>r</span>
        </div>
        <h1 className="login-title">{t('auth.loginPage.welcomeBack')}</h1>
        <p className="login-sub">{t('auth.loginPage.signInToGetTo')}</p>
        <ul className="login-apps" aria-hidden="true">
          <li><Mail />{' '}{t('common.mail2')}</li>
          <li><CalendarDays />{' '}{t('common.calendar')}</li>
          <li><CheckSquare />{' '}{t('common.tasks')}</li>
        </ul>
        {cfg.isLoading ? (
          <div className="login-loading"><Spinner /></div>
        ) : (
          <div className="stack login-actions">
            {error && (
              <div className="banner error" role="alert">
                {error}
              </div>
            )}
            {sso && (
              <a className="btn filled login-sso" href={buildUrl('/auth/oidc/start', { next })}>
                <KeyRound />{' '}{t('auth.loginPage.continueWith', { oidcName: cfg.data?.oidc_name || t('auth.loginPage.singleSignOn') })}
              </a>
            )}
            {sso && cfg.data?.local_login && (
              <div className="login-or">
                <span />
                {t('auth.loginPage.orUseAPassword')}
                <span />
              </div>
            )}
            {(cfg.data?.local_login ?? true) && (
              <form
                className="stack"
                onSubmit={(e) => {
                  e.preventDefault()
                  void submit()
                }}
              >
                <div className="field">
                  <label htmlFor="login-email">{t('common.email')}</label>
                  <input id="login-email" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus={!sso} />
                </div>
                <div className="field">
                  <label htmlFor="login-password">{t('common.password')}</label>
                  <input id="login-password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                <button type="submit" className={`btn ${sso ? 'tonal' : 'filled'} login-submit`} disabled={busy || !email || !password}>
                  {busy ? <Spinner small /> : <LogIn />}{' '}{t('common.signIn')}
                </button>
              </form>
            )}
            {cfg.isError && <div className="banner error">{t('auth.loginPage.cantReachTheMailrServer')}</div>}
          </div>
        )}
        <p className="login-foot">
          {window.location.host}
          {sso ? <>{' '}{t('auth.loginPage.securedBy', { oidcName: cfg.data?.oidc_name || t('auth.loginPage.singleSignOn') })}</> : null}
        </p>
      </main>
    </div>
  )
}

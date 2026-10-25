import { Filter, Paperclip, Calendar, Mail, Star, User, ArrowUpRight, X } from 'lucide-react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { accountTokens, hasToken, SEARCH_CHIPS, toggleToken } from '../../lib/search'
import { mailPath } from '../../lib/views'
import { AccountDot, useMailAccounts } from '../layout/accounts'
import { t } from '../../i18n'

const ICONS: Record<string, ReactNode> = {
  attachment: <Paperclip />,
  '7d': <Calendar />,
  unread: <Mail />,
  starred: <Star />,
  fromme: <User />,
  important: <ArrowUpRight />,
}

export function SearchChips({ q, view }: { q: string; view: string }) {
  const nav = useNavigate()
  const { accounts } = useMailAccounts()
  // The account the search is in (account:<address or id>), as a chip that takes it out again.
  // ACT mail.search_account @web.list
  const scoped = accountTokens(q).map((v) => ({ v, a: accounts.find((x) => x.address === v.toLowerCase() || String(x.id) === v || x.addresses.includes(v.toLowerCase())) }))
  return (
    <div className="search-chips no-print" role="group" aria-label={t('search.searchChips.refineSearch')}>
      {scoped.map(({ v, a }) => (
        <button key={`acct-${v}`} type="button" className="chip" aria-pressed data-testid="search-account-chip"
          aria-label={t('search.searchChips.searchAllAccounts', { address: a?.address ?? v })} data-tip={t('search.searchChips.searchAllAccounts', { address: a?.address ?? v })}
          onClick={() => nav(mailPath(view === 'search' ? 'search' : view, null, toggleToken(q, `account:${v}`) || undefined))}>
          {a && <AccountDot account={a} label={false} />}
          <span dir="auto">{t('search.searchChips.inAccount', { address: a?.address ?? v })}</span>
          <X aria-hidden />
        </button>
      ))}
      {SEARCH_CHIPS.map((c) => {
        const on = hasToken(q, c.token)
        return (
          <button key={c.id} type="button" className="chip" aria-pressed={on} onClick={() => nav(mailPath(view === 'search' ? 'search' : view, null, toggleToken(q, c.token) || undefined))}>
            {ICONS[c.id]}
            {c.label}
          </button>
        )
      })}
      <button type="button" className="chip" onClick={() => nav(`/settings/mail/filters?new=${encodeURIComponent(q)}`)}>
        <Filter />
        {t('common.createFilter')}
      </button>
    </div>
  )
}

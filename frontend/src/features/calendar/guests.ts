// Guest field warnings: "Dana is outside your organisation". My organisation is the
// domains of my addresses; a free-mail domain is no organisation, so it never warns.

import { t } from '../../i18n'

const FREE_MAIL = new Set([
  'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'yahoo.com', 'ymail.com',
  'icloud.com', 'me.com', 'mac.com', 'aol.com', 'proton.me', 'protonmail.com', 'pm.me', 'gmx.com', 'gmx.net',
  'fastmail.com', 'zoho.com', 'mail.com', 'yandex.com', 'hey.com', 'tutanota.com', 'purelymail.com',
])

const domainOf = (email: string) => email.slice(email.lastIndexOf('@') + 1).toLowerCase().trim()

/** The organisation's domains from my addresses (free-mail domains left out). */
export function orgDomains(myEmails: string[]): string[] {
  return [...new Set(myEmails.filter((e) => e.includes('@')).map(domainOf))].filter((d) => d && !FREE_MAIL.has(d))
}

/** A guest is outside when the domain is neither one of mine nor a subdomain of one. */
export function isOutside(email: string, domains: string[]): boolean {
  if (!domains.length || !email.includes('@')) return false
  const d = domainOf(email)
  return !domains.some((o) => d === o || d.endsWith(`.${o}`))
}

/** The warning line, or null: one guest by name, more as a count. */
export function outsideWarning(guests: { email: string; name?: string }[], domains: string[]): string | null {
  const out = guests.filter((g) => isOutside(g.email, domains))
  if (!out.length) return null
  if (out.length === 1) return t('calendar.guests.isOutsideYourOrganisation', { itemName: out[0].name || out[0].email })
  return t('calendar.guests.guestsAreOutsideYourOrganisation', { count: out.length })
}

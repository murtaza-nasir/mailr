// The three ways a mail account is connected (backend Connector.mode), with one name and one line
// each, the same in the setup guide, Settings → Mail → Mail accounts, the add-account wizard and
// the admin's managed accounts. The words "mirror", "store" and "import" stay internal.

import type { Connector, ConnectorMode } from '../../api/types'
import { t } from '../../i18n'

export function modeName(mode: ConnectorMode | string): string {
  if (mode === 'mirror') return t('settings.syncModes.keepInSync')
  if (mode === 'store') return t('settings.syncModes.mailrKeepsTheMail')
  if (mode === 'import') return t('settings.syncModes.copyOnce')
  return mode
}

export function modeLine(mode: ConnectorMode | string): string {
  if (mode === 'mirror') return t('settings.syncModes.keepInSyncLine')
  if (mode === 'store') return t('settings.syncModes.mailrKeepsTheMailLine')
  if (mode === 'import') return t('settings.syncModes.copyOnceLine')
  return ''
}

// Preset ids (backend connectors/presets.py) by the short name people know the provider by.
const PRESET_NAMES: Record<string, string> = {
  fastmail: 'Fastmail', migadu: 'Migadu', 'mailbox.org': 'mailbox.org', 'protonmail-bridge': 'Proton Mail', icloud: 'iCloud', // i18n-ignore: product names
  yahoo: 'Yahoo', gmail: 'Gmail', office365: 'Microsoft', zoho: 'Zoho', posteo: 'Posteo', // i18n-ignore: product names
}

/** The provider an account's mail is at, by name where it is known (Gmail, Fastmail), else its mail
 *  server's domain (example.com), for "Delete from Gmail after 14 days". */
export function providerName(c: Pick<Connector, 'kind' | 'preset' | 'config'>): string {
  if (c.preset && PRESET_NAMES[c.preset]) return PRESET_NAMES[c.preset]
  if (c.kind === 'gmail') return PRESET_NAMES.gmail
  if (c.kind === 'm365') return PRESET_NAMES.office365
  const cfg = c.config as { imap?: { host?: unknown }; email?: unknown }
  const host = String(cfg.imap?.host ?? '').toLowerCase()
  if (host.endsWith('gmail.com')) return PRESET_NAMES.gmail
  // A server name, not a bare host or an address (localhost, 127.0.0.1, a bridge on this machine).
  if (host.includes('.') && !/^[\d.]+$|^\[|:/.test(host)) return host.replace(/^(imap|mail|imap\.mail)\./, '')
  const domain = String(cfg.email ?? '').split('@')[1]?.toLowerCase()
  if (domain) return domain
  return t('settings.syncModes.theProvider')
}

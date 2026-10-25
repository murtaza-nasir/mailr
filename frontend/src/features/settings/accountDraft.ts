// The body of POST /connectors/test and POST /connectors for a mail account, shared by the
// add-account wizard (Settings → Mail → Mail accounts) and the setup guide's Connect your email step.

import type { ConnectorMode, ServerSettings, TestResult } from '../../api/types'

export interface AccountDraftInput {
  kind: string
  email: string
  displayName: string
  username: string
  password: string
  preset: string
  imap: ServerSettings
  smtp: ServerSettings
  mode: ConnectorMode
  localOnly: boolean
  retention: number
  /** Import history from this date (YYYY-MM-DD); empty for everything. */
  since: string
  /** With `since`: bring the older mail in afterwards, in the background. */
  backfill?: boolean
  gmail?: { spam: boolean; trash: boolean; skip: Set<string> }
  /** After a successful test: the folders to sync (non-Gmail accounts). */
  test?: TestResult | null
  folders?: Set<string>
  identities?: Set<string>
}

export function accountDraft(d: AccountDraftInput): Record<string, unknown> {
  const email = d.email.trim()
  return {
    kind: d.kind,
    name: email,
    preset: d.preset || null,
    mode: d.mode,
    data_class: d.localOnly ? 'local_only' : 'normal',
    retention_days: d.retention,
    config: {
      email,
      display_name: d.displayName.trim(),
      username: d.username.trim(),
      imap: d.imap,
      smtp: d.smtp,
      since: d.since || null,
      ...(d.since && d.backfill ? { backfill: true } : {}),
      ...(d.kind === 'gmail'
        ? { gmail: { include_spam: d.gmail?.spam ?? true, include_trash: d.gmail?.trash ?? false, exclude_labels: [...(d.gmail?.skip ?? [])] } }
        : d.test?.ok && d.test.folders.length && d.folders
          ? {
              // Unchecked folders are excluded; checked ones that are off by default are opted in.
              // New folders created upstream later are picked up automatically.
              folders: {
                exclude: d.test.folders.filter((f) => !d.folders!.has(f.name)).map((f) => f.name),
                also: d.test.folders.filter((f) => d.folders!.has(f.name) && f.sync === false).map((f) => f.name),
              },
            }
          : {}),
    },
    secrets: { password: d.password },
    ...(d.identities ? { identities: [...d.identities] } : {}),
  }
}

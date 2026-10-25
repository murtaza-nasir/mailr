import { assistantNameNow } from '../rail/assistant/useAssistantName'
// Admin → Users → "Prepare a new user": defaults for someone who has not signed in yet, by email
// address or by group (backend core/onboarding.py presets). They apply at the first sign-in, so
// the setup guide has fewer choices. Optional: without a preset the guide asks everything.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Trash2, UserCog } from 'lucide-react'
import { useState } from 'react'
import { api } from '../../api/client'
import type { AdminUser } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { formatRelative } from '../../lib/format'
import { toast, toastError } from '../../stores/ui'
import { THEMES } from '../../themes/registry'
import type { ThemeId } from '../../themes/types'
import { Check, Radio } from './common'
import { AccountFields, EMPTY_ACCOUNT, managedBody, useManagedSetting, type AccountFieldsValue, type PresetMailAccount } from './ManagedAccounts'
import { t as tr } from '../../i18n'

export interface Preset {
  id: string
  match: { email?: string; group?: string }
  defaults: { ui_theme?: ThemeId; theme?: string; density?: string; inbox_type?: string; mail_notifications?: string; desktop_notifications?: string; assistant?: boolean; notes?: boolean; calendar_url?: string }
  note: string
  used_by?: number | null
  used_at?: string | null
  applied?: number | null
  /** A mail account connected at the first sign-in (Admin → Users switch; email presets only). */
  mail_account?: PresetMailAccount | null
}

const INBOX: Record<string, string> = { simple: tr('common.simple'), important_first: tr('common.importantFirst'), default: tr('common.categories') }
const NOTIFY: Record<string, string> = { important: tr('common.importantMail'), vip: tr('settings.prepareUser.vipsOnly'), primary: tr('settings.prepareUser.importantAndPrimary'), all: tr('settings.prepareUser.everyEmail'), off: tr('common.none') }
const SCHEME: Record<string, string> = { system: tr('common.automatic'), light: tr('common.light'), dark: tr('common.dark') }
const DENSITY: Record<string, string> = { comfortable: tr('common.roomy'), default: tr('common.standard'), compact: tr('common.compact') }

/** A preset's defaults in a few words, for the list. */
export function presetSummary(p: Pick<Preset, 'defaults'>): string {
  const d = p.defaults
  const parts = [
    d.ui_theme && THEMES[d.ui_theme]?.name,
    d.theme && SCHEME[d.theme],
    d.density && DENSITY[d.density],
    d.inbox_type && tr('settings.prepareUser.inbox', { inbox: INBOX[d.inbox_type] }),
    // Presets saved before 7 Oct 2026 say desktop_notifications (the server reads both).
    (d.mail_notifications ?? d.desktop_notifications) && tr('settings.prepareUser.notify', { notify: NOTIFY[(d.mail_notifications ?? d.desktop_notifications)!] }),
    d.assistant === false ? tr('settings.prepareUser.noAssistantSteps') : d.assistant ? tr('settings.prepareUser.assistantSteps') : null,
    d.notes === false && tr('settings.prepareUser.noNotesIntro'),
    d.calendar_url && tr('settings.prepareUser.calendarServer'),
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : tr('settings.prepareUser.noDefaultsTheGuideAsks')
}

/** What happened to a preset's mail account, for the list. */
export function presetAccountText(a: PresetMailAccount | null | undefined): string | null {
  if (!a) return null
  if (a.connected_at) return tr('settings.prepareUser.mailAccountConnected', { address: a.address })
  if (a.skipped) return a.skipped
  return tr('settings.prepareUser.mailAccountAtTheirFirst', { address: a.address })
}

/** "Setup" in the users table. */
export function setupText(s: AdminUser['setup']): string {
  if (!s || s.status === 'none') return '—'
  if (s.status === 'done') return tr('settings.prepareUser.finished')
  if (s.status === 'pending') return tr('settings.prepareUser.notStarted')
  if (s.status === 'dismissed') return tr('settings.prepareUser.closedOf', { completed: s.completed, total: s.total })
  return tr('settings.prepareUser.inProgressOf', { completed: s.completed, total: s.total })
}

export function PresetsList() {
  const qc = useQueryClient()
  const q = useQuery<{ items: Preset[] }>({ queryKey: ['admin', 'onboarding-presets'], queryFn: () => api.get('/admin/onboarding/presets') })
  const [editing, setEditing] = useState<Preset | null | 'new'>(null)
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/admin/onboarding/presets/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['admin', 'onboarding-presets'] }),
    onError: (e) => toastError(e),
  })
  const items = q.data?.items ?? []
  return (
    <div className="prep-users" data-testid="prepared-users">
      <div className="row" style={{ alignItems: 'center', gap: 8, margin: '16px 0 4px' }}>
        <b style={{ flex: 1 }}>{tr('settings.prepareUser.preparedForNewUsers')}</b>
        <button type="button" className="btn tonal sm" onClick={() => setEditing('new')}>
          <UserCog />{' '}{tr('settings.prepareUser.prepareANewUser')}
        </button>
      </div>
      <p className="muted" style={{ fontSize: 13, margin: '0 0 8px' }}>
        {tr('settings.prepareUser.chooseSomeonesLookInboxAnd')}
      </p>
      {items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{tr('settings.prepareUser.for')}</th>
                <th>{tr('settings.prepareUser.defaults')}</th>
                <th>{tr('settings.prepareUser.used')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id}>
                  <td>
                    <b>{p.match.email ?? tr('settings.prepareUser.group', { group: p.match.group })}</b>
                    {p.note && <div className="muted" style={{ fontSize: 12 }}>{p.note}</div>}
                  </td>
                  <td style={{ fontSize: 13 }}>
                    {presetSummary(p)}
                    {p.mail_account && <div className="muted" data-testid="preset-mail-account">{presetAccountText(p.mail_account)}</div>}
                  </td>
                  <td style={{ fontSize: 13 }}>
                    {p.match.email ? (p.used_at ? tr('settings.prepareUser.signedIn', { relative: formatRelative(p.used_at) }) : 'waiting') : tr('settings.prepareUser.newUsers', { count: (p.applied ?? 0) })}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button type="button" className="icon-btn" aria-label={tr('settings.prepareUser.edit', { email: p.match.email ?? p.match.group })} data-tip={tr('common.edit2')} onClick={() => setEditing(p)}>
                      <Pencil />
                    </button>
                    <button type="button" className="icon-btn" aria-label={tr('settings.prepareUser.remove', { email: p.match.email ?? p.match.group })} data-tip={tr('common.remove')} onClick={() => del.mutate(p.id)}>
                      <Trash2 />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && <PresetDialog preset={editing === 'new' ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: Record<string, string> }) {
  const id = `pp-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{tr('settings.prepareUser.letThemChoose')}</option>
        {Object.entries(options).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </div>
  )
}

function PresetDialog({ preset, onClose }: { preset: Preset | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [by, setBy] = useState<'email' | 'group'>(preset?.match.group ? 'group' : 'email')
  const [who, setWho] = useState(preset?.match.email ?? preset?.match.group ?? '')
  const [note, setNote] = useState(preset?.note ?? '')
  const [d, setD] = useState<Preset['defaults']>(preset?.defaults ?? { assistant: true })
  const set = (k: keyof Preset['defaults'], v: string | boolean) => setD((x) => ({ ...x, [k]: v === '' ? undefined : v }))
  const managedOn = !!useManagedSetting().data?.enabled
  const prepared = preset?.mail_account ?? null
  const [withAccount, setWithAccount] = useState(!!prepared && !prepared.connected_at)
  const [acct, setAcct] = useState<AccountFieldsValue>(() => prepared ? {
    ...EMPTY_ACCOUNT, email: prepared.config.email ?? prepared.address, username: prepared.config.username ?? '',
    imap: prepared.config.imap ?? EMPTY_ACCOUNT.imap, smtp: prepared.config.smtp ?? EMPTY_ACCOUNT.smtp,
    mode: prepared.mode, localOnly: prepared.data_class === 'local_only',
  } : { ...EMPTY_ACCOUNT })
  const canAccount = managedOn && by === 'email' && !prepared?.connected_at
  const accountBody = (): Record<string, unknown> | undefined => {
    if (!canAccount) return by === 'group' && prepared ? {} : undefined
    if (!withAccount) return prepared ? {} : undefined
    return managedBody(acct)
  }
  const save = useMutation({
    mutationFn: () => {
      const mail = accountBody()
      const body = { match: by === 'email' ? { email: who.trim() } : { group: who.trim() }, defaults: d, note, ...(mail !== undefined ? { mail_account: mail } : {}) }
      return preset ? api.put(`/admin/onboarding/presets/${preset.id}`, body) : api.post('/admin/onboarding/presets', body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['admin', 'onboarding-presets'] })
      toast({ message: tr('settings.prepareUser.savedItAppliesWhenThey') })
      onClose()
    },
    onError: (e) => toastError(e, tr('common.notSaved')),
  })
  return (
    <Dialog
      open
      onClose={onClose}
      title={preset ? tr('settings.prepareUser.editWhatIsPrepared') : tr('settings.prepareUser.prepareANewUser')}
      modal
      footer={
        <>
          <button type="button" className="btn text" onClick={onClose}>
            {tr('common.cancel')}
          </button>
          <button type="button" className="btn filled" disabled={!who.trim() || save.isPending} onClick={() => save.mutate()}>
            {tr('common.save')}
          </button>
        </>
      }
    >
      <div className="stack">
        <div>
          <Radio name="pp-by" value="email" current={by} onChange={setBy} label={tr('settings.prepareUser.onePersonByEmailAddress')} desc={tr('settings.prepareUser.usedOnceAtTheirFirst')} />
          <Radio name="pp-by" value="group" current={by} onChange={setBy} label={tr('settings.prepareUser.everyoneInAGroup')} desc={tr('settings.prepareUser.eachNewMemberOfThis')} />
        </div>
        <div className="field">
          <label htmlFor="pp-who">{by === 'email' ? tr('settings.prepareUser.theirEmailAddressTheOne') : tr('settings.prepareUser.groupName')}</label>
          <input id="pp-who" className="input" type={by === 'email' ? 'email' : 'text'} value={who} onChange={(e) => setWho(e.target.value)} autoFocus />
        </div>
        <PrepareFields d={d} set={set} />
        {canAccount && (
          <div className="stack" style={{ gap: 8 }} data-testid="preset-account">
            <Check
              checked={withAccount}
              onChange={setWithAccount}
              label={tr('settings.prepareUser.connectAMailAccountFor')}
              desc={tr('settings.prepareUser.connectedAtTheirFirstSign')}
            />
            {withAccount && (
              <AccountFields v={acct} set={setAcct} passwordHint={prepared?.has_password ? tr('settings.prepareUser.leaveEmptyToKeepThe') : undefined} />
            )}
          </div>
        )}
        <div className="field">
          <label htmlFor="pp-note">{tr('settings.prepareUser.noteForYourselfOptional')}</label>
          <input id="pp-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
      </div>
    </Dialog>
  )
}

/** The setup defaults a preset or a household invite (InviteMember.tsx) can set. */
export function PrepareFields({ d, set }: { d: Preset['defaults']; set: (k: keyof Preset['defaults'], v: string | boolean) => void }) {
  const themes = Object.fromEntries(Object.values(THEMES).map((t) => [t.id, t.name]))
  return (
    <>
        <div className="grid-2">
          <Select label={tr('common.style')} value={d.ui_theme ?? ''} onChange={(v) => set('ui_theme', v)} options={themes} />
          <Select label={tr('common.lightOrDark')} value={d.theme ?? ''} onChange={(v) => set('theme', v)} options={SCHEME} />
          <Select label={tr('common.textAndSpacing')} value={d.density ?? ''} onChange={(v) => set('density', v)} options={DENSITY} />
          <Select label={tr('settings.prepareUser.inboxStyle')} value={d.inbox_type ?? ''} onChange={(v) => set('inbox_type', v)} options={INBOX} />
          <Select label={tr('common.notifications')} value={d.mail_notifications ?? d.desktop_notifications ?? ''} onChange={(v) => set('mail_notifications', v)} options={NOTIFY} />
        </div>
        <Check
          checked={d.assistant !== false}
          onChange={(v) => set('assistant', v)}
          label={tr('settings.prepareUser.introduce', { name: assistantNameNow() })}
          desc={tr('settings.prepareUser.theGuideOffersGetTo', { name: assistantNameNow() })}
        />
        <Check
          checked={d.notes !== false}
          onChange={(v) => set('notes', v)}
          label={tr('settings.prepareUser.introduceNotesAndLists')}
          desc={tr('settings.prepareUser.theLastScreenOffersTo')}
        />
        <div className="field">
          <label htmlFor="pp-cal">{tr('settings.prepareUser.calendarServerOptional')}</label>
          <input id="pp-cal" className="input" placeholder="https://cal.example.com/dav/" value={d.calendar_url ?? ''} onChange={(e) => set('calendar_url', e.target.value)} />
          <span className="hint">{tr('settings.prepareUser.theSetupGuideOffersThis')}</span>
        </div>
    </>
  )
}

// The sender screener (backend core/screener.py): first-time senders wait in Mail → Screener until
// the user allows, blocks or labels them. The data, the requests and the words the page shows.
//
// The list's query key sits under ['conversations'], so whatever refreshes the conversation lists
// (an action, new mail over the live stream) refreshes the Screener too.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { toast, toastError } from '../../stores/ui'
import { t } from '../../i18n'

export type AuthLevel = 'verified' | 'failed' | 'unknown'

export interface ScreenerSender {
  id: number
  /** An address, or a domain ("example.com"): what the decision is remembered by. */
  key: string
  kind: 'address' | 'domain'
  email: string
  name: string
  addresses: string[]
  messages: number
  conversations: number
  unread: number
  first_seen: string | null
  latest: { message_id: number; conversation_id: number; subject: string; snippet: string; date: string | null }
  auth: { level: AuthLevel; text: string }
  newsletter: boolean
  account: string
  local_only: boolean
}

export interface ScreenerList {
  enabled: boolean
  count: number
  senders: ScreenerSender[]
}

export interface ScreenerAccount {
  id: number
  name: string
  address: string
  local_only: boolean
  screened: boolean
  default: boolean
  reason: string
  chosen: boolean
}

export type DecidedStatus = 'allowed' | 'blocked' | 'label'

export interface ScreenerDecision {
  id: number
  key: string
  kind: 'address' | 'domain'
  email: string
  name: string
  status: DecidedStatus
  label_id: number | null
  label: string
  block_to: 'spam' | 'trash'
  decided_by: string
  decided_at: string | null
}

export interface ScreenerSettings {
  enabled: boolean
  accounts: ScreenerAccount[]
  decided: ScreenerDecision[]
}

export type Decision =
  | { decision: 'allow' }
  | { decision: 'block'; block_to?: 'spam' | 'trash' }
  | { decision: 'label'; label_id?: number; feed?: boolean }

export const screenerKey = ['conversations', 'screener'] as const
export const screenerSettingsKey = ['screener-settings'] as const

/** Who a decision covers: one address, or everyone at a company's domain. */
export function senderScope(s: Pick<ScreenerSender, 'kind' | 'key'>): string {
  return s.kind === 'domain' ? t('mail.screener.everyoneAt', { key: s.key }) : t('mail.screener.thisAddressOnly')
}

/** The name a card shows: the display name, else the address. */
export function senderTitle(s: Pick<ScreenerSender, 'name' | 'email'>): string {
  return s.name.trim() || s.email
}

/** "3 messages in 2 conversations", "1 message". */
export function heldText(s: Pick<ScreenerSender, 'messages' | 'conversations'>): string {
  const m = t('mail.screener.messages', { count: s.messages })
  return s.conversations > 1 ? t('mail.screener.inConversations', { m, count: s.conversations }) : m
}

/** What a decision did, for the toast. */
export function doneText(d: Decision, who: string, label?: string | null): string {
  if (d.decision === 'allow') return t('mail.screener.allowedTheirMailIsIn', { who })
  if (d.decision === 'block') return t('mail.screener.blockedTheirMailGoesTo', { who, value: d.block_to === 'trash' ? t('common.trash') : t('common.spam') })
  return t('mail.screener.goesToOutOfYour', { who, label: label || (d.feed ? t('mail.screener.feed') : t('mail.screener.theLabel')) })
}

/** A past decision in words (Settings → Mail → Spam & Screener). */
export function decisionText(d: Pick<ScreenerDecision, 'status' | 'label' | 'block_to'>): string {
  if (d.status === 'allowed') return t('mail.screener.allowed')
  if (d.status === 'blocked') return d.block_to === 'trash' ? t('mail.screener.blockedToTrash') : t('mail.screener.blockedToSpam')
  return d.label ? t('mail.screener.to', { label: d.label }) : t('mail.screener.toADeletedLabel')
}

/** Whether the navigation shows Mail → Screener: while it is on. Turning it off moves the waiting
 *  mail to the Inbox (a server job), so nothing is left behind it. The page itself still opens
 *  from its address, with a way to turn it on. */
export function showScreenerNav(enabled: boolean | undefined): boolean {
  return !!enabled
}

export function useScreener() {
  return useQuery<ScreenerList>({
    queryKey: screenerKey,
    queryFn: ({ signal }) => api.get<ScreenerList>('/screener', undefined, signal),
  })
}

export function useScreenerSettings() {
  return useQuery<ScreenerSettings>({
    queryKey: screenerSettingsKey,
    queryFn: ({ signal }) => api.get<ScreenerSettings>('/screener/settings', undefined, signal),
  })
}

function useRefresh() {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: ['conversations'] })
    void qc.invalidateQueries({ queryKey: ['labels'] })
    void qc.invalidateQueries({ queryKey: screenerSettingsKey })
  }
}

/** Allow, block or label one sender. The card leaves the list at once. */
export function useDecide() {
  const qc = useQueryClient()
  const refresh = useRefresh()
  return useMutation({
    mutationFn: ({ sender, d }: { sender: ScreenerSender; d: Decision; who: string }) =>
      api.post<{ status: string; affected: number; label: string | null }>(`/screener/${sender.id}/decide`, d),
    onMutate: async ({ sender }) => {
      await qc.cancelQueries({ queryKey: screenerKey })
      const prev = qc.getQueryData<ScreenerList>(screenerKey)
      if (prev) {
        const senders = prev.senders.filter((s) => s.id !== sender.id)
        qc.setQueryData<ScreenerList>(screenerKey, { ...prev, senders, count: senders.length })
      }
      return { prev }
    },
    onSuccess: (res, { d, who }) => toast({ message: doneText(d, who, res.label) }),
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(screenerKey, ctx.prev)
      toastError(e, t('common.notChanged'))
    },
    onSettled: refresh,
  })
}

/** Undo a decision: the sender is screened again. */
export function useForget() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (d: ScreenerDecision) => api.del<unknown>(`/screener/${d.id}`),
    onSuccess: (_r, d) => toast({ message: t('mail.screener.willBeScreenedAgain', { key: d.key }) }),
    onError: (e) => toastError(e, t('common.notChanged')),
    onSettled: refresh,
  })
}

/** Turn the Screener on or off, or choose its accounts. */
export function useSaveScreenerSettings() {
  const qc = useQueryClient()
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: { enabled?: boolean; accounts?: Record<string, boolean> }) =>
      api.put<ScreenerSettings>('/screener/settings', body),
    onSuccess: (s) => {
      qc.setQueryData(screenerSettingsKey, s)
      void qc.invalidateQueries({ queryKey: ['me'] })
    },
    onError: (e) => toastError(e, t('mail.screener.settingsNotSaved')),
    onSettled: refresh,
  })
}

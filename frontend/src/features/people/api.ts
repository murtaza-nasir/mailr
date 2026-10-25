// People and contact groups through /api/v1: ranked people, groups (manual,
// accepted suggestions, address-book groups), suggested groups, detail suggestions from
// signatures, the address-book status and places for the event editor.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, asList } from '../../api/client'
import type { ConversationSummary } from '../../api/types'
import { toastError } from '../../stores/ui'

export interface Phone { value: string; type?: string; source?: string }
export interface PostalAddress { text: string; street?: string; city?: string; region?: string; postcode?: string; country?: string; type?: string; source?: string }

export interface PersonRow {
  /** Empty for a person Ambrose knows who is not a contact (`entity_id` is set). */
  email: string
  entity_id?: number
  no_mail?: boolean
  emails?: string[]
  name: string
  organization?: string
  title?: string
  received_count?: number
  sent_count?: number
  last_seen?: string | null
  last_sent?: string | null
  strength?: number
  two_way?: boolean
  phones?: Phone[]
  addresses?: PostalAddress[]
  birthday?: string
  sources?: Record<string, string>
}

export interface DetailSuggestion {
  id: number
  field: 'phone' | 'title' | 'organization' | 'address'
  value: string
  method: 'rules' | 'model'
  message_id: number | null
  local_only: boolean
}

export interface PersonCard extends PersonRow {
  recent_conversations?: ConversationSummary[]
  groups?: { id: number; name: string; colour: string | null; source: GroupSource }[]
  suggestions?: DetailSuggestion[]
  /** What Ambrose knows: the person entry linked to this contact and its notes. */
  knowledge?: { entity: import('../settings/assistant/types').Entity | null; memories: import('../settings/assistant/types').Memory[] }
}

export type GroupSource = 'manual' | 'suggested' | 'carddav'

export interface Member { email: string; name: string }

export interface Group {
  id: number
  name: string
  colour: string | null
  source: GroupSource
  state: 'active' | 'suggested' | 'dismissed'
  read_only: boolean
  count: number
  members: Member[]
  name_hint?: string
  evidence?: { messages?: number; last?: string | null; subjects?: string[] }
}

export interface AddressBookStatus {
  connector_id: number
  name: string
  url: string | null
  available: boolean | null
  checked_at: string | null
  error: string
  cards: number
  groups: number
  books: { name: string; synced_at: string | null; error: string }[]
}

export interface Place { email: string; name: string; text: string; guest: boolean }

export const peopleKeys = {
  all: ['people'] as const,
  list: (q: string, limit: number) => ['people', 'list', q, limit] as const,
  card: (email: string) => ['people', 'card', email] as const,
  groups: (state: string, q: string) => ['groups', state, q] as const,
  books: ['address-books'] as const,
  places: (emails: string, q: string) => ['places', emails, q] as const,
}

/** The People list, strongest first (person.find). */
export function usePeopleList(q: string, limit: number) {
  return useQuery<PersonRow[]>({
    queryKey: peopleKeys.list(q, limit),
    queryFn: async ({ signal }) => asList<PersonRow>(await api.get<unknown>('/people', { q, limit }, signal)),
    placeholderData: keepPreviousData,
  })
}

export function usePersonCard(email: string | null) {
  return useQuery<PersonCard>({
    queryKey: peopleKeys.card(email ?? ''),
    queryFn: ({ signal }) => api.get<PersonCard>(`/people/${encodeURIComponent(email!)}`, undefined, signal),
    enabled: !!email,
  })
}

/** Groups (active or suggested); with `q`, the groups whose name contains it (for recipient fields). */
export function useGroups(q = '', state: 'active' | 'suggested' = 'active', enabled = true) {
  return useQuery<Group[]>({
    queryKey: peopleKeys.groups(state, q),
    queryFn: async ({ signal }) => asList<Group>(await api.get<unknown>('/groups', { q, state }, signal)),
    enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  })
}

export function useAddressBooks() {
  return useQuery<AddressBookStatus[]>({
    queryKey: peopleKeys.books,
    queryFn: async ({ signal }) => asList<AddressBookStatus>(await api.get<unknown>('/address-books', undefined, signal)),
  })
}

/** Postal addresses from People for the event editor's location field (event.suggest_location). */
export function usePlaces(guestEmails: string[]) {
  const emails = guestEmails.map((e) => e.toLowerCase()).sort().join(',')
  return useQuery<Place[]>({
    queryKey: peopleKeys.places(emails, ''),
    queryFn: async ({ signal }) => asList<Place>(await api.get<unknown>('/contacts/places', { emails, limit: 20 }, signal)),
    staleTime: 60_000,
  })
}

/** Group writes: create, rename / recolour / members, delete, accept and dismiss suggestions. */
export function useGroupMutations() {
  const qc = useQueryClient()
  const done = () => {
    void qc.invalidateQueries({ queryKey: ['groups'] })
    void qc.invalidateQueries({ queryKey: peopleKeys.all })
  }
  const opts = { onSuccess: done, onError: (e: unknown) => toastError(e) }
  return {
    // ACT group.create @web.people
    create: useMutation({ mutationFn: (b: { name: string; members: (Member | string)[]; colour?: string | null }) => api.post<Group>('/groups', b as unknown as Record<string, unknown>), ...opts }),
    // ACT group.rename @web.people, ACT group.add_members @web.people, ACT group.remove_members @web.people
    update: useMutation({
      mutationFn: ({ id, ...b }: { id: number; name?: string; colour?: string | null; add?: (Member | string)[]; remove?: string[] }) =>
        api.patch<Group>(`/groups/${id}`, b as unknown as Record<string, unknown>),
      ...opts,
    }),
    // ACT group.delete @web.people
    remove: useMutation({ mutationFn: (id: number) => api.del<{ ok: boolean }>(`/groups/${id}`), ...opts }),
    // ACT group.accept_suggestion @web.people
    accept: useMutation({ mutationFn: ({ id, name, members }: { id: number; name: string; members?: string[] }) => api.post<Group>(`/groups/${id}/accept`, { name, ...(members ? { members } : {}) }), ...opts }),
    // ACT group.dismiss_suggestion @web.people
    dismiss: useMutation({ mutationFn: (id: number) => api.post<{ ok: boolean }>(`/groups/${id}/dismiss`), ...opts }),
    // ACT group.suggest @web.people
    refresh: useMutation({ mutationFn: () => api.post<{ new: number; suggestions: Group[] }>('/groups/suggestions/refresh'), ...opts }),
  }
}

/** Accept or dismiss a detail found in a signature (person.accept_detail, person.dismiss_detail). */
export function useDetailDecision(email: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, accept }: { id: number; accept: boolean }) =>
      api.post(`/people/${encodeURIComponent(email)}/suggestions/${id}/${accept ? 'accept' : 'dismiss'}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: peopleKeys.all }),
    onError: (e: unknown) => toastError(e),
  })
}

/** Read the address books now (contacts.sync); read-only. */
export function useSyncAddressBooks() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async () => asList<AddressBookStatus>(await api.post<unknown>('/address-books/sync')),
    onSuccess: (data) => {
      qc.setQueryData(peopleKeys.books, data)
      void qc.invalidateQueries({ queryKey: peopleKeys.all })
      void qc.invalidateQueries({ queryKey: ['groups'] })
    },
    onError: (e: unknown) => toastError(e),
  })
}

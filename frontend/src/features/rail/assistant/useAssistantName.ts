// The assistant's name (Settings → Ambrose → About you). "Ambrose" until
// the profile has loaded or when it is not set.

import { useQuery } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { qkA, type AssistantProfile } from '../../settings/assistant/types'
import { DEFAULT_ASSISTANT_NAME, rememberAssistantName } from './assistantName'

export { DEFAULT_ASSISTANT_NAME, assistantNameNow, withAssistantName } from './assistantName'

export function useAssistantName(): string {
  const q = useQuery<AssistantProfile>({
    queryKey: qkA.profile,
    queryFn: () => api.get<AssistantProfile>('/assistant/profile'),
    staleTime: 5 * 60_000,
  })
  const name = q.data?.options?.assistant_name?.trim() || DEFAULT_ASSISTANT_NAME
  rememberAssistantName(name)
  return name
}

/** A rail/app title: the assistant's entry shows its name. */
export function appTitle(a: { id: string; title: string }, name: string): string {
  return a.id === 'agent' ? name : a.title
}

// The setup guide's API (backend api/onboarding_routes.py).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { qk } from '../../api/hooks'
import type { Mark, OnboardingState, StepId } from './steps'

export const qkOnboarding = ['onboarding'] as const

export function useOnboarding(enabled = true) {
  return useQuery<OnboardingState>({ queryKey: qkOnboarding, queryFn: ({ signal }) => api.get<OnboardingState>('/onboarding', undefined, signal), enabled, staleTime: 30_000 })
}

export interface ProgressPatch {
  step?: StepId
  mark?: Partial<Record<StepId, Mark>>
  status?: 'active' | 'done' | 'dismissed'
}

/** Save progress. The query cache takes the answer; /me is refreshed so the shell sees the status. */
export function useSaveProgress() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: ProgressPatch) => api.put<OnboardingState>('/onboarding', p as Record<string, unknown>),
    onSuccess: (s) => {
      qc.setQueryData(qkOnboarding, s)
      void qc.invalidateQueries({ queryKey: qk.me })
    },
  })
}

export function useRestartGuide() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<OnboardingState>('/onboarding/restart'),
    onSuccess: (s) => {
      qc.setQueryData(qkOnboarding, s)
      void qc.invalidateQueries({ queryKey: qk.me })
    },
  })
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface AboutSummary {
  profile: { preferred_name: string; role: string; about: string; writing: string; signoff_name: string; languages: string }
  signature: string
  memories: { text: string; kind: string }[]
}

export const chatApi = {
  turn: (messages: ChatTurn[]) => api.post<{ reply: string; done: boolean }>('/onboarding/chat', { messages } as unknown as Record<string, unknown>),
  summary: (messages: ChatTurn[]) => api.post<AboutSummary>('/onboarding/chat/summary', { messages } as unknown as Record<string, unknown>),
  save: (body: { profile: Record<string, string>; memories: { text: string; kind: string }[]; signature?: string | null; source: 'fields' | 'chat' }) =>
    api.post<{ profile: string[]; memories: number[]; signatures: number[] }>('/onboarding/about', body as unknown as Record<string, unknown>),
}

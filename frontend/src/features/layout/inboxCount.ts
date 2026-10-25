// The Inbox count query (lib/inboxCount.ts, ACT settings.inbox_count @web.nav).

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useSettings } from '../../api/hooks'
import { inboxCountMode, type InboxCount, type InboxCountMode } from '../../lib/inboxCount'

/** GET /inbox/count. The key sits under ['labels'], so whatever refreshes the label counts (mail
 *  changes, live events) refreshes it too; the layout and the setting are in the key, so changing
 *  either fetches again. */
export function useInboxCount(): { mode: InboxCountMode; data: InboxCount | undefined } {
  const settings = useSettings()
  const mode = inboxCountMode(settings.inbox_count)
  const q = useQuery<InboxCount>({
    queryKey: ['labels', 'inbox-count', settings.inbox_type, mode],
    queryFn: ({ signal }) => api.get<InboxCount>('/inbox/count', undefined, signal),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  })
  return { mode, data: q.data }
}

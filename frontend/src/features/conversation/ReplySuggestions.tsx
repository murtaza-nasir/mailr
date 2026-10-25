// mail.reply_suggestions: three short replies under the last personal email of a conversation; a
// click starts the reply with that text (the user edits and sends it as usual). Off in Settings →
// General. ACT mail.reply_suggestions @web.reader

import { useQuery } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { api } from '../../api/client'
import { useSettings } from '../../api/hooks'
import type { Message } from '../../api/types'
import { t } from '../../i18n'

export function ReplySuggestions({ m, onPick }: { m: Message; onPick: (text: string) => void }) {
  const on = useSettings().reply_suggestions !== false
  const q = useQuery({
    queryKey: ['reply-suggestions', m.id],
    queryFn: () => api.get<{ status: string; replies: string[] }>(`/messages/${m.id}/reply-suggestions`),
    enabled: on,
    staleTime: Infinity,
    retry: false,
  })
  const replies = q.data?.replies ?? []
  if (!on || !replies.length) return null
  return (
    <div className="reply-suggestions no-print" role="group" aria-label={t('conversation.replySuggestions.suggestedReplies')}>
      <Sparkles size={16} aria-hidden />
      {replies.map((r) => (
        <button key={r} type="button" className="chip reply-suggestion" onClick={() => onPick(r)}>{r}</button>
      ))}
    </div>
  )
}

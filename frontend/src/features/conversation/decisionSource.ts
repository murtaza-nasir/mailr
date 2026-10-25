// The Why panel's "who decided" line (ConversationView WhyButton): a filter, you, mailr's rules,
// or which kind of model and which one. A chat model is "Chat model"; a Jev-protocol model is
// "Decision model".

import type { Decision } from '../../api/types'
import { t } from '../../i18n'

export const SMART_NAMES: Record<string, string> = { needs_reply: t('common.needsReply'), waiting_on: t('conversation.decisionSource.waitingOn'), deadline: t('conversation.decisionSource.deadline') }
export const SOURCE_NAMES: Record<string, string> = {
  llm: t('conversation.decisionSource.chatModel'), decision_model: t('conversation.decisionSource.decisionModel'), classifier: t('conversation.decisionSource.classifier'), heuristic: t('conversation.decisionSource.rules'), provider: t('conversation.decisionSource.yourMailProvider'), vip: t('conversation.decisionSource.vipSender'),
}

/** Who decided: a filter, you, or which kind of model and which one. A deadline's source_ref is its
 * date, so the model is named in its reason ("… · from jev (jev-latest)"). */
export function decisionSource(d: Pick<Decision, 'kind' | 'target' | 'source' | 'source_ref'>): string {
  if (d.source === 'rule') return t('conversation.decisionSource.filter', { value: d.source_ref ? `“${d.source_ref}”` : '' })
  if (d.source === 'user') return t('common.you')
  const who = SOURCE_NAMES[d.source] ?? t('conversation.decisionSource.classifier')
  if (d.kind === 'smart' && d.target === 'deadline') return d.source_ref ? t('conversation.decisionSource.due', { who, sourceRef: d.source_ref }) : who
  return `${who}${d.source_ref ? ` (${d.source_ref})` : ''}`
}


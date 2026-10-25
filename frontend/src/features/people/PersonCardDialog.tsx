// The person card as a dialog, for the places outside Mail → People & groups: the Memory page's
// People section, a person Ambrose looked up in a chat, and the Used memories line. A contact
// opens its card (details, groups, mail, What Ambrose knows); a person Ambrose knows who is not a
// contact opens their entry. ACT person.card @web.settings

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Skeleton } from '../../components/bits'
import { Dialog } from '../../components/Dialog'
import { EntityDialog } from '../settings/assistant/EntityCard'
import { qkA, type EntityDetail } from '../settings/assistant/types'
import { PersonCardView } from './PeopleTab'
import { t } from '../../i18n'

export function PersonCardDialog({ email, entityId, onClose }: { email?: string; entityId?: number; onClose: () => void }) {
  const detail = useQuery<EntityDetail>({
    queryKey: qkA.entity(entityId ?? 0),
    queryFn: () => api.get<EntityDetail>(`/assistant/entities/${entityId}`),
    enabled: !email && entityId != null,
  })
  const address = email ?? detail.data?.contacts[0]?.email ?? null
  if (!address && entityId != null && detail.data) return <EntityDialog id={entityId} initialKind="person" onClose={onClose} />
  return (
    <Dialog open wide onClose={onClose} title={t('people.card.title')}>
      {address ? <PersonCardView email={address} onCompose={onClose} /> : <Skeleton height={200} />}
    </Dialog>
  )
}

import { Archive, Inbox, OctagonAlert, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { useFavoriteEntries } from '../../features/layout/favorites'
import type { NavEntry } from '../../features/layout/mailboxes'
import { useDragging, useDropTarget } from '../../features/mail/dragDrop'
import { t as tr } from '../../i18n'

// Focus has no sidebar. While conversations are dragged, a quiet shelf of targets slides in
// under the top bar: the favorites, then Inbox, Archive, Spam and Trash.
const PLACES: { view: string; title: string; icon: ReactNode }[] = [
  { view: 'inbox', title: tr('common.inbox'), icon: <Inbox /> },
  { view: 'all', title: tr('common.archive'), icon: <Archive /> },
  { view: 'spam', title: tr('common.spam'), icon: <OctagonAlert /> },
  { view: 'trash', title: tr('common.trash'), icon: <Trash2 /> },
]

function ShelfTarget({ view, title, icon }: { view: string; title: string; icon: ReactNode }) {
  const t = useDropTarget(view, 'below')
  return (
    <div className={`f-shelf-target${t.over ? ' drop-over' : ''}`} data-drop-view={view} {...t.props}>
      {icon}
      <span>{title}</span>
    </div>
  )
}

export function DropShelf() {
  const dragging = useDragging()
  const favorites = useFavoriteEntries()
  if (!dragging) return null
  const favViews = new Set(favorites.map((e) => e.view))
  const rest = PLACES.filter((p) => !favViews.has(p.view))
  return (
    <div className="f-shelf no-print" role="group" aria-label={tr('themes.focus.dropShelf.dropOnAFolderOr')}>
      {favorites.map((e: NavEntry) => (
        <ShelfTarget key={e.view} view={e.view} title={e.title} icon={e.icon} />
      ))}
      {favorites.length > 0 && rest.length > 0 && <span className="f-shelf-sep" aria-hidden />}
      {rest.map((p) => (
        <ShelfTarget key={p.view} {...p} />
      ))}
    </div>
  )
}

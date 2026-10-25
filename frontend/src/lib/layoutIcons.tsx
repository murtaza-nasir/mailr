import {
  Bell, Book, Bookmark, Briefcase, Clock, Flag, Folder, Heart, Home, Inbox, Info, Laptop, Mail, MessageSquare,
  ShoppingBag, Star, Tag, Users, Zap,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { t } from '../i18n'

/** Icons a layout tab can use (the names match core/layouts.py ICONS). */
export const LAYOUT_ICONS: Record<string, { icon: ReactNode; label: string }> = {
  inbox: { icon: <Inbox />, label: t('common.inbox') },
  tag: { icon: <Tag />, label: t('common.tag') },
  users: { icon: <Users />, label: t('common.people') },
  info: { icon: <Info />, label: t('lib.layoutIcons.info') },
  message: { icon: <MessageSquare />, label: t('common.conversation') },
  bag: { icon: <ShoppingBag />, label: t('lib.layoutIcons.shopping') },
  star: { icon: <Star />, label: t('common.star') },
  clock: { icon: <Clock />, label: t('lib.layoutIcons.clock') },
  bookmark: { icon: <Bookmark />, label: t('lib.layoutIcons.bookmark') },
  briefcase: { icon: <Briefcase />, label: t('common.work') },
  home: { icon: <Home />, label: t('lib.layoutIcons.home') },
  flag: { icon: <Flag />, label: t('common.flag') },
  zap: { icon: <Zap />, label: t('lib.layoutIcons.lightning') },
  laptop: { icon: <Laptop />, label: t('lib.layoutIcons.laptop') },
  book: { icon: <Book />, label: t('common.reading') },
  heart: { icon: <Heart />, label: t('lib.layoutIcons.heart') },
  bell: { icon: <Bell />, label: t('lib.layoutIcons.bell') },
  folder: { icon: <Folder />, label: t('lib.layoutIcons.folder') },
  mail: { icon: <Mail />, label: t('common.mail2') },
}

export function layoutIcon(name?: string | null): ReactNode {
  return (name && LAYOUT_ICONS[name]?.icon) || <Inbox />
}

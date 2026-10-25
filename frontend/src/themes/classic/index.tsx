import { ConversationRow } from '../../features/mail/ConversationRow'
import type { ThemeDef } from '../types'
import { ClassicShell } from './ClassicShell'
import { ClassicPreview } from '../previews'
import { t } from '../../i18n'

export const classicTheme: ThemeDef = {
  id: 'classic',
  name: t('themes.classic.index.classic'),
  tagline: t('themes.classic.index.roomyWithLabelsInA'),
  description: t('themes.classic.index.labelsInASidebarOne'),
  Preview: ClassicPreview,
  Shell: ClassicShell,
  apps: { eventDetail: 'popover', taskDetail: 'pane', noteDetail: 'popover', taskList: 'list' },
  list: {
    Row: ConversationRow,
    rowHeight: (narrow) => (narrow ? 72 : 40),
    toolbar: 'full',
    tabsPlacement: 'inside',
  },
}

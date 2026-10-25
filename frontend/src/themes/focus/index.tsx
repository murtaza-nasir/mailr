import { FocusPreview } from '../previews'
import type { ThemeDef } from '../types'
import { FocusRow } from './FocusRow'
import { FocusShell } from './FocusShell'
import { t } from '../../i18n'

export const focusTheme: ThemeDef = {
  id: 'focus',
  name: t('themes.focus.index.focus'),
  tagline: t('themes.focus.index.keyboardFirstOneCalmColumn'),
  description: t('themes.focus.index.noSidebarGoAnywhereAnd'),
  Preview: FocusPreview,
  Shell: FocusShell,
  apps: { eventDetail: 'card', taskDetail: 'card', noteDetail: 'card', taskList: 'list' },
  list: {
    Row: FocusRow,
    rowHeight: (narrow) => (narrow ? 76 : 52),
    toolbar: 'full',
    tabsPlacement: 'top',
  },
  // One column: conversations replace the list.
  readingPane: () => 'none',
}

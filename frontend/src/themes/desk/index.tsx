import { DeskPreview } from '../previews'
import { arrangeSorted } from '../shared/grouping'
import type { ThemeDef } from '../types'
import { DeskHeader, DeskRow } from './DeskTable'
import { DeskShell } from './DeskShell'
import { t } from '../../i18n'

export const deskTheme: ThemeDef = {
  id: 'desk',
  name: t('themes.desk.index.desk'),
  tagline: t('themes.desk.index.denseWithASortableTable'),
  description: t('themes.desk.index.folderPaneWithUnreadAnd'),
  Preview: DeskPreview,
  Shell: DeskShell,
  apps: { eventDetail: 'popover', taskDetail: 'below', noteDetail: 'below', taskList: 'table' },
  list: {
    Row: DeskRow,
    rowHeight: (narrow) => (narrow ? 44 : 26),
    arrange: arrangeSorted,
    ListHeader: DeskHeader,
    toolbar: 'minimal',
    tabsPlacement: 'top',
    tabIndicators: 'compact',
  },
  // Preview below the table on wide screens; phones open conversations full screen.
  readingPane: () => 'below',
  paneChoices: ['below', 'right', 'none'],
}

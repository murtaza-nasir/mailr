import { OfficePreview } from '../previews'
import { arrangeByDate } from '../shared/grouping'
import type { ThemeDef } from '../types'
import { OfficeGroupHead, OfficeRow } from './OfficeRow'
import { OfficeShell } from './OfficeShell'
import { t } from '../../i18n'

export const officeTheme: ThemeDef = {
  id: 'office',
  name: t('themes.office.index.workspace'),
  tagline: t('themes.office.index.threePanesARibbonAnd'),
  description: t('themes.office.index.folderTreeARibbonOf'),
  Preview: OfficePreview,
  Shell: OfficeShell,
  apps: { eventDetail: 'pane', taskDetail: 'pane', noteDetail: 'pane', taskList: 'list' },
  list: {
    Row: OfficeRow,
    rowHeight: () => 76,
    arrange: arrangeByDate,
    GroupHead: OfficeGroupHead,
    toolbar: 'minimal',
    tabsPlacement: 'top',
  },
  // The reading pane is the shell's own (tabbed); on phones conversations open full screen.
  readingPane: () => 'right',
  externalReader: true,
}

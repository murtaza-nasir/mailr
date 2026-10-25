import { CardsPreview } from '../previews'
import { arrangeByBundle } from '../shared/grouping'
import type { ThemeDef } from '../types'
import { BundleHead, CardRow } from './CardRow'
import { CardsShell } from './CardsShell'
import { t } from '../../i18n'

export const cardsTheme: ThemeDef = {
  id: 'cards',
  name: t('themes.cards.index.cards'),
  tagline: t('themes.cards.index.bundledCardsAndColour'),
  description: t('themes.cards.index.peopleFirstNotificationsAndNewsletters'),
  Preview: CardsPreview,
  Shell: CardsShell,
  apps: { eventDetail: 'sheet', taskDetail: 'sheet', noteDetail: 'sheet', taskList: 'list' },
  list: {
    Row: CardRow,
    rowHeight: () => 112,
    arrange: arrangeByBundle,
    GroupHead: BundleHead,
    toolbar: 'full',
    tabsPlacement: 'top',
  },
  // A reading card beside the list on wide screens; one at a time on smaller ones.
  readingPane: (wide) => (wide ? 'right' : 'none'),
  paneChoices: ['right', 'none'],
}

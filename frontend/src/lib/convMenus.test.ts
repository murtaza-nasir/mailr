// ACT-SURFACES: web.list
// The conversation menus' submenus (lib/convMenus.ts): Find mail, Filter mail like this, Show in tab (mail.show_in_tab) and Category (mail.category).
import { describe, expect, it } from 'vitest'
import { shouldRemove } from '../api/hooks'
import type { InboxLayout, LayoutSection } from '../api/types'
import { bareSubject, CATEGORY_TABS, domainOf, filterChoices, findChoices, listIdOf, offersShowInTab, sharedCategory, shownInText } from './convMenus'

const people = [
  { email: 'ana@acme.io', name: 'Ana Ruiz' },
  { email: 'me@hale.example', me: true },
  { email: 'bo@acme.io' },
]

describe('find and filter choices', () => {
  it('strips reply and forward prefixes', () => {
    expect(bareSubject('Re: Fwd: RE: Q3 plan')).toBe('Q3 plan')
    expect(bareSubject('AW: Angebot')).toBe('Angebot')
  })

  it('offers no domain for shared webmail', () => {
    expect(domainOf('x@gmail.com')).toBeNull()
    expect(domainOf('x@acme.io')).toBe('acme.io')
  })

  it('finds from the sender, the domain, the subject and the people', () => {
    const c = findChoices(people, 'Re: Q3 plan')
    expect(c.map((x) => [x.label, x.q])).toEqual([
      ['From Ana Ruiz', 'from:ana@acme.io'],
      ['From @acme.io', 'from:@acme.io'],
      ['With this subject', 'subject:"Q3 plan"'],
      ['With these people', '{from:ana@acme.io,bo@acme.io to:ana@acme.io,bo@acme.io cc:ana@acme.io,bo@acme.io}'],
    ])
  })

  it('filters by mailing list only when the message has a List-Id', () => {
    expect(filterChoices(people, 'News').map((x) => x.id)).toEqual(['sender', 'domain', 'subject'])
    const c = filterChoices(people, 'News', 'Acme announcements <announce.acme.io>')
    expect(c.find((x) => x.id === 'list')?.q).toBe('list:announce.acme.io')
    expect(listIdOf('plain.list.id')).toBe('plain.list.id')
  })

  it('items that open the filter editor end with an ellipsis', () => {
    expect(filterChoices(people, 'News').every((x) => x.label.endsWith('…'))).toBe(true)
    expect(findChoices(people, 'News').some((x) => x.label.endsWith('…'))).toBe(false)
  })
})

const sec = (base: string, category?: string): LayoutSection =>
  ({ id: 's', title: 's', base, category: category ?? null, filter: null, q: '', limit: null, exclude_above: true, inbox_only: false })
const layout = (tabs: [string, LayoutSection[]][]): InboxLayout => ({
  id: 'l', name: 'L', tabs: tabs.map(([name, sections]) => ({ id: name.toLowerCase(), name, icon: null, badge: 'none', indicators: [], sections })),
})

describe('mail.show_in_tab: Show in tab', () => {
  it('is offered only when the layout has more than one tab', () => {
    expect(offersShowInTab(null)).toBe(false)
    expect(offersShowInTab(layout([['Inbox', [sec('inbox')]]]))).toBe(false)
    expect(offersShowInTab(layout([['Inbox', [sec('inbox')]], ['GitHub', [sec('label:12')]]]))).toBe(true)
  })

  it('takes the row out of the tab it leaves, not out of the tab it goes to', () => {
    const req = { ids: [1], action: 'show_in_tab' as const, tab: 'github' }
    expect(shouldRemove({ view: 'inbox', layout: 'mine', tab: 'inbox', sec: 'inbox' }, req)).toBe(true)
    expect(shouldRemove({ view: 'inbox', layout: 'mine', tab: 'github', sec: 'github' }, req)).toBe(false)
    expect(shouldRemove({ view: 'search', q: 'x' }, req)).toBe(false)
  })

  it('says where the conversations now show', () => {
    expect(shownInText('Github', 1)).toBe('Shown in Github.')
    expect(shownInText('Promotions', 3)).toBe('3 conversations shown in Promotions.')
  })
})

describe('mail.category: the Category submenu', () => {
  it('lists the six categories, Primary first', () => {
    expect(Object.values(CATEGORY_TABS).map((c) => c.name)).toEqual(['Primary', 'Promotions', 'Social', 'Updates', 'Forums', 'Purchases'])
  })

  it('marks one category when every conversation shares it (no category = Primary)', () => {
    expect(sharedCategory([{ category: null }])).toBe('primary')
    expect(sharedCategory([{ category: 'social' }, { category: 'social' }])).toBe('social')
    expect(sharedCategory([{ category: 'social' }, { category: null }])).toBeNull()
    expect(sharedCategory([])).toBeNull()
  })
})

// ACT-SURFACES: web.note web.panel
// The assistant on notes: agent.ask_about from the note
// editor attaches the note and its selected items as focused_note; the Notes app sends its screen
// context by ids; [[note:…]] citations open the note with the item focused; the steps show a note icon.
import { afterEach, describe, expect, it } from 'vitest'
import { appContext } from '../assistant-context/appContext'
import { askContext, askLabel, askSuggestions } from '../assistant-context/askAbout'
import { citationRoute, streamingText, type Citation } from '../rail/assistant/citations'
import { parseMarker } from '../rail/assistant/citeGrammar'
import { stepIcon } from '../rail/assistant/steps'
import { useNotesUI } from './store'

afterEach(() => useNotesUI.getState().set({ focusedItem: null, selected: new Set() }))

describe('agent.ask_about on a note', () => {
  it('sends the note and its selected items as focused_note', () => {
    const about = { kind: 'note' as const, id: 'n3k9x2m4q7ab', title: 'Shopping', list: true, items: ['i1', 'i2'] }
    expect(askContext(about)).toEqual({ top: { focused_note: { id: 'n3k9x2m4q7ab', items: ['i1', 'i2'] } } })
    expect(askLabel(about)).toBe('2 items of Shopping')
    expect(askSuggestions(about)[0]).toBe('Turn these into tasks')
    const whole = { ...about, items: [] }
    expect(askContext(whole)).toEqual({ top: { focused_note: { id: 'n3k9x2m4q7ab' } } })
    expect(askLabel({ ...whole, title: '' })).toBe('(untitled list)')
    expect(askSuggestions(whole)[0]).toBe('Sort this list by shop')
  })
})

describe('the Notes screen context', () => {
  it('names the view, the open note, the focused item and the selected cards, by id', () => {
    useNotesUI.getState().set({ focusedItem: { note: 'n3k9x2m4q7ab', item: 'item-1' } })
    useNotesUI.getState().setSelected(new Set(['n2222222222a']))
    expect(appContext('/notes/n/n3k9x2m4q7ab', '', '')).toEqual({
      notes: { view: 'all', focused: 'n3k9x2m4q7ab', focused_item: 'item-1', selected: ['n2222222222a'] },
    })
    expect(appContext('/notes/n/n3k9x2m4q7ab', '', '#i-item-9').notes?.focused_item).toBe('item-9')
    expect(appContext('/notes/label/Home', '', '').notes).toMatchObject({ view: 'label', label: 'Home' })
    expect(appContext('/notes/search', '?q=milk', '').notes).toMatchObject({ view: 'search', q: 'milk' })
  })
})

describe('note citations', () => {
  const base: Citation = {
    n: 1, kind: 'note', conversation_id: null, message_id: null, subject: 'Shopping', from: 'List', date: null,
    quote: null, verified: true, start: null, end: null, url: null, note_id: 'n3k9x2m4q7ab', item_id: null,
  }
  it('parse with an item and hide while streaming', () => {
    expect(parseMarker('note:n3k9x2m4q7ab#0192f0c4-7b1e')).toEqual({ kind: 'note', id: 'n3k9x2m4q7ab', at: '0192f0c4-7b1e', quote: null })
    expect(streamingText('Milk is there [[note:n3k9x2m4q7ab#abc]] already.')).toBe('Milk is there already.')
  })
  it('open the note, with the item focused', () => {
    expect(citationRoute(base)).toBe('/notes/n/n3k9x2m4q7ab')
    expect(citationRoute({ ...base, item_id: 'i 1' })).toBe('/notes/n/n3k9x2m4q7ab#i-i%201')
  })
  it('show the note icon on the note tools', () => {
    expect(stepIcon('checklist_items')).toBe('note')
    expect(stepIcon('search_notes')).toBe('note')
  })
})

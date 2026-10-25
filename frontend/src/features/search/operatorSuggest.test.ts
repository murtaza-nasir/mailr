import { describe, expect, it } from 'vitest'
import { IN_FOLDERS, lastToken, matchNames, operatorValue, operatorWord } from './operatorSuggest'

describe('search operator suggestions', () => {
  it('recognises the operators that have suggestions', () => {
    expect(operatorWord('from:')).toEqual({ op: 'from', partial: '' })
    expect(operatorWord('from:mei')).toEqual({ op: 'from', partial: 'mei' })
    expect(operatorWord('label:"Blue')).toEqual({ op: 'label', partial: 'Blue' })
    expect(operatorWord('in:sp')).toEqual({ op: 'in', partial: 'sp' })
    expect(operatorWord('subject:x')).toBeNull()
    expect(operatorWord('-from:x')).toBeNull()
    expect(operatorWord('mei')).toBeNull()
  })
  it('an open quote keeps the words after an operator together', () => {
    expect(lastToken('invoice label:"Bluefin Ou')).toBe('label:"Bluefin Ou')
    expect(lastToken('invoice from:mei')).toBe('from:mei')
    expect(lastToken('label:"Bluefin Outdoor" in')).toBe('in')
  })
  it('quotes a value with a space', () => {
    expect(operatorValue('label', 'Bluefin Outdoor')).toBe('label:"Bluefin Outdoor"')
    expect(operatorValue('in', 'spam')).toBe('in:spam')
  })
  it('names that start with the text come first', () => {
    expect(matchNames(['Clients', 'Bluefin Outdoor', 'Blue team'], 'blue')).toEqual(['Bluefin Outdoor', 'Blue team'])
    expect(matchNames(IN_FOLDERS, 's')).toEqual(['sent', 'starred', 'snoozed', 'scheduled', 'spam', 'drafts'])
  })
})

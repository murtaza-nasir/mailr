import { describe, expect, it } from 'vitest'
import { mayRestoreFocus } from './useModalFocus'

// Stand-ins for elements: a tag name and what they contain.
const node = (tagName: string, inside: unknown[] = []) => ({ tagName, contains: (x: unknown) => inside.includes(x) }) as unknown as Element

describe('focus back on close', () => {
  it('only from inside the closing element, or from nowhere', () => {
    const button = node('BUTTON')
    const card = node('DIV', [button])
    const field = node('INPUT')
    expect(mayRestoreFocus(null, card)).toBe(true)
    expect(mayRestoreFocus(node('BODY'), card)).toBe(true)
    expect(mayRestoreFocus(button, card)).toBe(true)
    // The editor opened by Edit has the focus: the card leaving later does not take it.
    expect(mayRestoreFocus(field, card)).toBe(false)
    expect(mayRestoreFocus(field, null)).toBe(false)
  })
})

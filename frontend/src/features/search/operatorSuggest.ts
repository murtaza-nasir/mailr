// Suggestions after a search operator: from:, to:, cc:, bcc: offer people;
// label: offers the user's labels; in: offers the folders. Picking one completes the word in the
// box (the search runs on Enter), as Gmail, Outlook and Fastmail complete them.

export type PeopleOp = 'from' | 'to' | 'cc' | 'bcc'
export interface OperatorWord {
  op: PeopleOp | 'label' | 'in'
  /** What follows the colon, without a leading quote. */
  partial: string
}

/** The folders in: takes (backend core/search.py SYSTEM_IN, without the synonyms). */
export const IN_FOLDERS = ['inbox', 'sent', 'drafts', 'starred', 'snoozed', 'important', 'scheduled', 'archive', 'spam', 'trash', 'anywhere']

/** The word being typed: the last word, or an operator with an open quote (label:"Bluefin Ou). */
export function lastToken(value: string): string {
  const quoted = /(?:^|\s)(-?(?:from|to|cc|bcc|label|in):"[^"]*)$/i.exec(value)
  return quoted ? quoted[1] : (value.split(/\s+/).pop() ?? '')
}

/** The operator being typed as the last word, if it is one that has suggestions. */
export function operatorWord(lastWord: string): OperatorWord | null {
  const m = /^(-?)(from|to|cc|bcc|label|in):"?([^"]*)$/i.exec(lastWord)
  if (!m || m[1]) return null
  return { op: m[2].toLowerCase() as OperatorWord['op'], partial: m[3] }
}

/** The word to put in the box for a value: quoted when it holds a space. */
export function operatorValue(op: OperatorWord['op'], value: string): string {
  return /\s/.test(value) ? `${op}:"${value}"` : `${op}:${value}`
}

/** Labels (user labels, by name) whose name starts with, then contains, the partial text. */
export function matchNames(names: string[], partial: string, max = 6): string[] {
  const p = partial.toLowerCase()
  const starts = names.filter((n) => n.toLowerCase().startsWith(p))
  const inside = names.filter((n) => !n.toLowerCase().startsWith(p) && n.toLowerCase().includes(p))
  return [...starts, ...inside].slice(0, max)
}

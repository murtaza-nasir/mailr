// The order of the To/Cc/Bcc suggestions. The server ranks people by how much they
// write with the user. On top of that, a person whose first name, last name or address
// starts with what is typed comes before one where it only appears inside a word: "dan" puts Dana
// Whitfield above Jordan Lee, so Enter picks the person meant.

interface Person {
  email: string
  name?: string | null
}

/** Whether `q` starts a word of the name or the address's local part (or the address). */
export function prefixMatch(p: Person, q: string): boolean {
  const s = q.trim().toLowerCase()
  if (!s) return false
  const words = (p.name ?? '').toLowerCase().split(/[\s.,'’()-]+/).filter(Boolean)
  const email = p.email.toLowerCase()
  const local = email.split('@')[0]
  return words.some((w) => w.startsWith(s)) || email.startsWith(s) || local.split(/[._+-]+/).some((w) => w.startsWith(s))
}

/** Prefix matches first, each group in the server's order (strength). */
export function rankPeople<P extends Person>(people: P[], q: string): P[] {
  const first: P[] = []
  const rest: P[] = []
  for (const p of people) (prefixMatch(p, q) ? first : rest).push(p)
  return [...first, ...rest]
}

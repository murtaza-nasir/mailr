// Contact groups in recipient fields (compose and the calendar guest field).
// Choosing a group adds its members one by one; the field remembers which addresses the group
// added, so it can show one summary chip ("Committee · 4 of 5") with the members under it,
// remove one member, or remove the whole group. Addresses that were already there before the
// group was chosen stay when the group goes.

import { t as tr } from '../../i18n'

export interface Expanded {
  id: number
  name: string
  /** The addresses this group added (lower case), in member order. */
  emails: string[]
  /** How many members the group had when it was chosen. */
  total: number
}

const low = (e: string) => e.trim().toLowerCase()

/** Add a group's members to `value`: returns the new value and the expansion record. */
export function addGroup<T extends { email: string }>(value: T[], group: { id: number; name: string }, members: T[]): { value: T[]; expanded: Expanded } {
  const have = new Set(value.map((v) => low(v.email)))
  const added: T[] = []
  for (const m of members) {
    const e = low(m.email)
    if (!e || have.has(e)) continue
    have.add(e)
    added.push(m)
  }
  return { value: [...value, ...added], expanded: { id: group.id, name: group.name, emails: added.map((a) => low(a.email)), total: members.length } }
}

export interface Shown<T> {
  group: Expanded
  members: T[]
}

/** Split `value` into the expanded groups (members still present) and the other addresses.
 * A group whose members are all gone is left out. */
export function partition<T extends { email: string }>(value: T[], groups: Expanded[]): { groups: Shown<T>[]; loose: T[] } {
  const byEmail = new Map(value.map((v) => [low(v.email), v] as const))
  const claimed = new Set<string>()
  const shown: Shown<T>[] = []
  for (const g of groups) {
    const members = g.emails.filter((e) => byEmail.has(e) && !claimed.has(e)).map((e) => byEmail.get(e)!)
    members.forEach((m) => claimed.add(low(m.email)))
    if (members.length) shown.push({ group: g, members })
  }
  return { groups: shown, loose: value.filter((v) => !claimed.has(low(v.email))) }
}

/** Remove a group: the addresses it added go; the others stay. */
export function removeGroup<T extends { email: string }>(value: T[], group: Expanded): T[] {
  const drop = new Set(group.emails)
  return value.filter((v) => !drop.has(low(v.email)))
}

/** Keep only the expansion records that still have a member in `value`. */
export function prune<T extends { email: string }>(value: T[], groups: Expanded[]): Expanded[] {
  const have = new Set(value.map((v) => low(v.email)))
  return groups.filter((g) => g.emails.some((e) => have.has(e)))
}

/** "Committee · 5" or, after removals, "Committee · 4 of 5". */
export function groupLabel(g: Expanded, present: number): string {
  return present === g.total ? `${g.name} · ${present}` : tr('people.groupChips.of', { itemName: g.name, present, total: g.total })
}

/** Groups to offer for what was typed: name contains the text; the ones already chosen are left out. */
export function groupOptions<G extends { id: number; name: string; count: number }>(groups: G[], text: string, chosen: Expanded[], max = 3): G[] {
  const t = text.trim().toLowerCase()
  if (!t) return []
  const used = new Set(chosen.map((c) => c.id))
  return groups
    .filter((g) => g.count > 0 && !used.has(g.id) && g.name.toLowerCase().includes(t))
    .sort((a, b) => Number(b.name.toLowerCase().startsWith(t)) - Number(a.name.toLowerCase().startsWith(t)))
    .slice(0, max)
}

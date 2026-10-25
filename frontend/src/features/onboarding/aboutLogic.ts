// "About you": the few simple fields as what the assistant profile stores, and the chat's
// summary as an editable draft. Pure, tested in aboutLogic.test.ts.

import type { AboutSummary } from './api'
import { t as tr } from '../../i18n'

export const TONES = [tr('onboarding.aboutLogic.warm'), tr('onboarding.aboutLogic.friendly'), tr('onboarding.aboutLogic.shortAndToThePoint'), tr('onboarding.aboutLogic.formal')] as const
export type Tone = (typeof TONES)[number]

export interface AboutFields {
  name: string
  role: string
  tones: Tone[]
  writing: string
  signoff: string
  notes: string
}

export const EMPTY_FIELDS: AboutFields = { name: '', role: '', tones: [], writing: '', signoff: '', notes: '' }

/** "Love, Rosa" → the signature lines ("Love,\nRosa") and the name she signs with ("Rosa"). */
export function parseSignoff(text: string): { signature: string; name: string } {
  const t = text.trim()
  if (!t) return { signature: '', name: '' }
  let lines = t.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  if (lines.length === 1) {
    const m = /^(.*?,)\s*(\S.*)$/.exec(lines[0])
    if (m && m[1].length <= 30) lines = [m[1], m[2]]
  }
  const name = lines.length > 1 ? lines[lines.length - 1] : /^[\p{L}][\p{L} .'-]{0,40}$/u.test(lines[0]) && lines[0].split(/\s+/).length <= 3 ? lines[0] : ''
  return { signature: lines.join('\n'), name }
}

/** The writing preferences sentence from the tone chips and her own words. */
export function writingText(tones: Tone[], extra: string): string {
  const parts: string[] = []
  if (tones.length) parts.push(`${tones.join(', ')}.`.replace(/^./, (c) => c.toUpperCase()))
  if (extra.trim()) parts.push(extra.trim())
  return parts.join(' ')
}

export interface AboutSave {
  profile: Record<string, string>
  memories: { text: string; kind: string }[]
  signature: string | null
}

/** The fields form as POST /onboarding/about. Empty fields are left out, so nothing is cleared. */
export function fieldsToSave(f: AboutFields): AboutSave {
  const profile: Record<string, string> = {}
  if (f.name.trim()) profile.preferred_name = f.name.trim()
  if (f.role.trim()) profile.role = f.role.trim()
  const w = writingText(f.tones, f.writing)
  if (w) profile.writing = w
  const so = parseSignoff(f.signoff)
  if (so.name) profile.signoff_name = so.name
  if (f.notes.trim()) profile.about = f.notes.trim()
  return { profile, memories: [], signature: so.signature || null }
}

/** The chat's proposal, as she edits it before saving. */
export interface SummaryDraft {
  preferred_name: string
  role: string
  about: string
  writing: string
  signature: string
  memories: { text: string; kind: string; keep: boolean }[]
}

export function draftFromSummary(s: AboutSummary): SummaryDraft {
  return {
    preferred_name: s.profile.preferred_name ?? '',
    role: s.profile.role ?? '',
    about: s.profile.about ?? '',
    writing: s.profile.writing ?? '',
    signature: s.signature || (s.profile.signoff_name ? s.profile.signoff_name : ''),
    memories: (s.memories ?? []).map((m) => ({ ...m, keep: true })),
  }
}

export function draftToSave(d: SummaryDraft): AboutSave {
  const profile: Record<string, string> = {}
  for (const k of ['preferred_name', 'role', 'about', 'writing'] as const) if (d[k].trim()) profile[k] = d[k].trim()
  const so = parseSignoff(d.signature)
  if (so.name) profile.signoff_name = so.name
  return {
    profile,
    memories: d.memories.filter((m) => m.keep && m.text.trim()).map((m) => ({ text: m.text.trim(), kind: m.kind })),
    signature: so.signature || null,
  }
}

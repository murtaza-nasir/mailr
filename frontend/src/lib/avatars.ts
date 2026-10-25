// Sender pictures: GET /api/v1/avatars/{email} (server-side lookup, cached there).
//
// The user's setting `sender_pictures` (off by default) decides whether the client asks about
// strangers at all. Addresses of this instance's users (yours and your family's) have pictures
// on the server itself (an identity's own picture, else that user's account picture):
// GET /pictures/local lists them as SHA-256 hashes with a version, and the client asks only
// for those, whatever the setting. Addresses that had no picture are remembered for the
// session, so a list does not re-request them on every render.

import { useSyncExternalStore } from 'react'
import type { Me } from '../api/types'

let enabled = false
const listeners = new Set<() => void>()
const missing = new Set<string>()

export function setSenderPictures(v: boolean) {
  if (v === enabled) return
  enabled = v
  for (const l of listeners) l()
}

export function useSenderPictures(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => enabled,
  )
}

export function avatarUrl(email: string): string {
  return `/api/v1/avatars/${encodeURIComponent(email.trim().toLowerCase())}`
}

export function knownMissing(key: string): boolean {
  return missing.has(key)
}

export function markMissing(key: string) {
  missing.add(key)
}

/** Forget cached misses (after the user uploads a picture or turns lookups on). */
export function resetMissing() {
  missing.clear()
}

// ---------- pictures of this instance's users ----------

let localKeys: Record<string, number> = {}
const hashes = new Map<string, string>()
const pending = new Set<string>()
let localVersion = 0
const localListeners = new Set<() => void>()

function bumpLocal() {
  localVersion++
  for (const l of localListeners) l()
}

/** The hashed addresses with a picture on this server (GET /pictures/local `keys`). */
export function setLocalPictures(keys: Record<string, number>) {
  localKeys = keys ?? {}
  bumpLocal()
}

export async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

function hashOf(email: string): string | undefined {
  const e = email.trim().toLowerCase()
  const h = hashes.get(e)
  if (h) return h
  if (!pending.has(e) && globalThis.crypto?.subtle) {
    pending.add(e)
    sha256Hex(e)
      .then((hex) => {
        hashes.set(e, hex)
        if (localKeys[hex]) bumpLocal()
      })
      .catch(() => undefined)
      .finally(() => pending.delete(e))
  }
  return undefined
}

/** The picture version of an address of this instance's users, or null (not known yet, or none). */
export function localPictureVersion(email: string | null | undefined): number | null {
  if (!email) return null
  const h = hashOf(email)
  return h && localKeys[h] ? localKeys[h] : null
}

export function useLocalPicture(email: string | null | undefined): number | null {
  useSyncExternalStore(
    (cb) => {
      localListeners.add(cb)
      return () => localListeners.delete(cb)
    },
    () => localVersion,
  )
  return localPictureVersion(email)
}

/**
 * Your own picture for an address you send from (the default one when none is given): the
 * identity's own picture, else your account picture, else null (initials).
 */
export function ownPictureUrl(me: Pick<Me, 'identities' | 'picture'> | null | undefined, email?: string | null): string | null {
  if (!me) return null
  const ids = me.identities ?? []
  const ident = email ? ids.find((i) => i.email.toLowerCase() === email.trim().toLowerCase()) : (ids.find((i) => i.is_default) ?? ids[0])
  if (email && !ident) return null
  if (ident?.has_avatar) return `/api/v1/identities/${ident.id}/avatar`
  if (me.picture?.has) return `/api/v1/me/picture?v=${me.picture.version}`
  return null
}

// "Always display images from" this sender: the senders kept in this browser, and whether a message's
// remote images show when it opens. Apart from MessageBody.tsx, so the list's prefetch (prefetch.ts)
// can ask for the same body the reader will without loading the reader.

import { imagesAllowedByDefault } from '../../lib/html'

const trustedSenders = new Set<string>()
function loadTrusted() {
  try {
    for (const s of JSON.parse(localStorage.getItem('mailr.imagesAllowed') ?? '[]') as string[]) trustedSenders.add(s)
  } catch {
    /* ignore */
  }
}
loadTrusted()

export function trustSender(email: string) {
  trustedSenders.add(email)
  try {
    localStorage.setItem('mailr.imagesAllowed', JSON.stringify([...trustedSenders].slice(-500)))
  } catch {
    /* ignore */
  }
}

/** Whether a message's remote images show when it opens (the setting, Spam, "Always display images
 *  from" this sender). The list's prefetch asks for the same body the reader will (prefetch.ts). */
export function imagesShownByDefault(setting: 'always' | 'ask' | 'never' | undefined, spam: boolean, sender: string): boolean {
  return imagesAllowedByDefault(setting, spam, trustedSenders.has(sender))
}
